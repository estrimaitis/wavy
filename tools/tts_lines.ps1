# Renders the vocal lines for the synthetic test song with the built-in Windows voices.
param([string]$OutDir)
Add-Type -AssemblyName System.Speech
New-Item -ItemType Directory -Force $OutDir | Out-Null
$lines = @(
  @("male",   "I walked along the river in the cold November rain"),
  @("female", "And I could hear you calling out my name"),
  @("male",   "The city lights were fading as the morning came"),
  @("female", "But nothing in this world will ever be the same"),
  @("male",   "So hold on to the moment, hold on to the night"),
  @("female", "We are dancing in the shadows till the morning light")
)
$i = 0
foreach ($l in $lines) {
  $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
  if ($l[0] -eq "male") { $s.SelectVoice("Microsoft David Desktop") } else { $s.SelectVoice("Microsoft Zira Desktop") }
  $s.Rate = -1
  $fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(44100, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
  $s.SetOutputToWaveFile((Join-Path $OutDir ("line{0}_{1}.wav" -f $i, $l[0])), $fmt)
  $s.Speak($l[1])
  $s.Dispose()
  $i++
}
