# Renders a batch of TTS lines described in a JSON file: [{"voice": "male"|"female", "text": "...", "pitch": "+0%", "out": "path.wav"}]
param([string]$Spec)
Add-Type -AssemblyName System.Speech
$items = Get-Content -Raw -Encoding UTF8 $Spec | ConvertFrom-Json
foreach ($it in $items) {
  $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
  $voice = if ($it.voice -eq "female") { "Microsoft Zira Desktop" } else { "Microsoft David Desktop" }
  $fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(44100, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
  $s.SetOutputToWaveFile($it.out, $fmt)
  $text = [System.Security.SecurityElement]::Escape($it.text)
  $ssml = "<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='en-US'><voice name='$voice'><prosody rate='-10%' pitch='$($it.pitch)'>$text</prosody></voice></speak>"
  $s.SpeakSsml($ssml)
  $s.Dispose()
}
