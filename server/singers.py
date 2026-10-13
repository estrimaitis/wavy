"""Split a (dry, lead) vocal stem into one track per singer.

Voice-separation models can't tell *who* is singing, so we diarize instead:
  1. find sung regions by energy,
  2. embed ~1.5 s windows with a speaker-verification network (WavLM x-vectors) and
     measure their pitch,
  3. cluster the windows into singers (automatically picking how many, 1-4),
  4. turn the per-frame labels into smooth gain masks that partition the stem,
     so the singer tracks always sum back to the original vocal.

Simultaneous singing (harmonies, call-and-response overlaps) goes to whoever dominates
that moment. The lead/backing split upstream already pulls most harmony parts away.
"""
from dataclasses import dataclass

import numpy as np

from . import audio_io

FRAME = 0.02          # label resolution (s)
WIN = 1.5             # embedding window (s)
HOP = 0.5
MIN_SEGMENT = 0.6     # shorter singer turns get merged into a neighbour (s)
FADE = 0.04           # crossfade between singers (s)
MAX_SINGERS = 4


@dataclass
class Singer:
    index: int
    median_f0: float
    seconds: float

    @property
    def register(self) -> str:
        if self.median_f0 <= 0:
            return "voice"
        if self.median_f0 < 165:
            return "lower voice"
        if self.median_f0 > 190:
            return "higher voice"
        return "mid voice"


_embedder = None


def _load_embedder():
    global _embedder
    if _embedder is None:
        import torch
        from transformers import AutoFeatureExtractor, WavLMForXVector

        name = "microsoft/wavlm-base-plus-sv"
        device = "cuda" if torch.cuda.is_available() else "cpu"
        try:  # skip the network round-trip once the model is cached
            extractor = AutoFeatureExtractor.from_pretrained(name, local_files_only=True)
            model = WavLMForXVector.from_pretrained(name, local_files_only=True)
        except OSError:
            extractor = AutoFeatureExtractor.from_pretrained(name)
            model = WavLMForXVector.from_pretrained(name)
        _embedder = (extractor, model.to(device).eval(), device)
    return _embedder


def _embed(chunks: list[np.ndarray]) -> np.ndarray:
    import torch

    extractor, model, device = _load_embedder()
    out = []
    for i in range(0, len(chunks), 32):
        batch = extractor(chunks[i:i + 32], sampling_rate=16000, return_tensors="pt", padding=True)
        with torch.no_grad():
            emb = model(**{k: v.to(device) for k, v in batch.items()}).embeddings
        out.append(torch.nn.functional.normalize(emb, dim=-1).cpu().numpy())
    return np.concatenate(out)


def _voiced_frames(mono16: np.ndarray) -> np.ndarray:
    hop = int(FRAME * 16000)
    n = len(mono16) // hop
    frames = mono16[: n * hop].reshape(n, hop)
    db = 10 * np.log10(np.mean(frames ** 2, axis=1) + 1e-12)
    peak = np.percentile(db, 99) if n else 0
    return db > max(peak - 38, -60)


def _pitch(chunk: np.ndarray) -> float:
    import librosa

    f0 = librosa.yin(chunk, fmin=70, fmax=1000, sr=16000, frame_length=1024)
    rms = librosa.feature.rms(y=chunk, frame_length=1024, hop_length=256)[0][: len(f0)]
    f0 = f0[: len(rms)]
    good = f0[(rms > rms.max() * 0.2) & (f0 > 75) & (f0 < 950)]
    return float(np.median(good)) if len(good) else 0.0


def _cluster(emb: np.ndarray, f0: np.ndarray) -> np.ndarray:
    from sklearn.cluster import AgglomerativeClustering
    from sklearn.metrics import silhouette_score

    n = len(emb)
    if n < 4:
        return np.zeros(n, dtype=int)
    cos = 1 - emb @ emb.T
    logf = np.log2(np.where(f0 > 0, f0, np.median(f0[f0 > 0]) if np.any(f0 > 0) else 200))
    pitch = np.abs(logf[:, None] - logf[None, :])
    dist = np.clip(cos + 0.35 * np.minimum(pitch, 1.5), 0, None)
    np.fill_diagonal(dist, 0)

    best_labels, best_score = np.zeros(n, dtype=int), 0.0
    for k in range(2, min(MAX_SINGERS, n - 1) + 1):
        labels = AgglomerativeClustering(n_clusters=k, metric="precomputed", linkage="average").fit_predict(dist)
        if np.bincount(labels).min() < max(2, 0.06 * n):
            continue
        score = silhouette_score(dist, labels, metric="precomputed")
        if score > best_score + 0.03:
            best_labels, best_score = labels, score
    # Below this, the "clusters" are one voice moving around its range.
    return best_labels if best_score >= 0.22 else np.zeros(n, dtype=int)


def _smooth(labels: np.ndarray) -> np.ndarray:
    """Merge singer turns shorter than MIN_SEGMENT into the neighbouring turn."""
    min_len = int(MIN_SEGMENT / FRAME)
    changed = True
    while changed:
        changed = False
        bounds = np.flatnonzero(np.diff(labels)) + 1
        starts = np.concatenate([[0], bounds])
        ends = np.concatenate([bounds, [len(labels)]])
        if len(starts) <= 1:
            break
        lengths = ends - starts
        i = int(np.argmin(lengths))
        if lengths[i] >= min_len:
            break
        left = labels[starts[i] - 1] if i > 0 else None
        right = labels[ends[i]] if ends[i] < len(labels) else None
        if left is None:
            fill = right
        elif right is None:
            fill = left
        else:
            fill = left if lengths[i - 1] >= lengths[i + 1] else right
        labels[starts[i]:ends[i]] = fill
        changed = True
    return labels


def split(vocal: np.ndarray) -> tuple[list[np.ndarray], list[Singer], np.ndarray]:
    """Returns (per-singer audio, singer info, frame labels at FRAME resolution, -1 for silence)."""
    mono16 = audio_io.to_mono_16k(vocal)
    voiced = _voiced_frames(mono16)
    n_frames = len(voiced)
    win, hop = int(WIN / FRAME), int(HOP / FRAME)

    starts = [s for s in range(0, max(n_frames - win, 0) + 1, hop) if voiced[s:s + win].mean() > 0.35]
    if len(starts) < 4:
        silence = np.where(voiced, 0, -1)
        return [vocal], [Singer(0, _pitch(mono16) if voiced.any() else 0.0, float(voiced.sum() * FRAME))], silence

    samples = int(FRAME * 16000)
    chunks = [mono16[s * samples:(s + win) * samples] for s in starts]
    labels_win = _cluster(_embed(chunks), np.array([_pitch(c) for c in chunks]))

    # Frame labels: each voiced frame takes the label of the window centred nearest to it.
    centres = np.array(starts) + win // 2
    right = np.searchsorted(centres, np.arange(n_frames)).clip(1, len(centres) - 1)
    left = right - 1
    nearest = np.where(np.arange(n_frames) - centres[left] <= centres[right] - np.arange(n_frames), left, right)
    labels = labels_win[nearest]
    labels = _smooth(labels)

    # Renumber singers by first appearance.
    order = []
    for lab in labels[voiced]:
        if lab not in order:
            order.append(lab)
    remap = {old: new for new, old in enumerate(order)}
    labels = np.array([remap.get(l, 0) for l in labels])
    k = max(len(order), 1)

    # Gain masks with short crossfades, interpolated to audio rate; they sum to exactly 1 everywhere,
    # so the singer tracks add back up to the original vocal.
    n = len(vocal)
    sample_t = np.arange(n) / audio_io.SR
    frame_t = (np.arange(n_frames) + 0.5) * FRAME
    fade = max(int(FADE / FRAME), 1)
    kernel = np.ones(fade) / fade
    tracks, singers = [], []
    for s in range(k):
        m = (labels == s).astype(np.float64)
        m = np.convolve(np.pad(m, (fade // 2, fade - 1 - fade // 2), mode="edge"), kernel, mode="valid")
        tracks.append(m)
    total = np.sum(tracks, axis=0)
    total[total == 0] = 1
    out = []
    for s, m in enumerate(tracks):
        gain = np.interp(sample_t, frame_t, m / total).astype(np.float32)
        audio = vocal * gain[:, None]
        sung = (labels == s) & voiced
        mono_s = mono16[: len(sung) * samples].reshape(-1, samples)[sung[: len(mono16) // samples]].ravel()
        singers.append(Singer(s, _pitch(mono_s) if len(mono_s) > 1600 else 0.0, float(sung.sum() * FRAME)))
        out.append(audio)

    labelled = np.where(voiced, labels, -1)
    return out, singers, labelled
