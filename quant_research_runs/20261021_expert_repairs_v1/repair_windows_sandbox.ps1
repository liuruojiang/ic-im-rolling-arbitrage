[CmdletBinding()]
param([switch]$Apply)
$ErrorActionPreference = 'Stop'
$configPath = 'D:\Codex\home\config.toml'
$expectedSha = 'a657c549c59bf07009219d9075d86c1d37ee8458b992ab82c09e82dbb9d6b4c1'
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) { throw 'Expected Codex configuration not found.' }
$originalBytes = [IO.File]::ReadAllBytes($configPath)
$actualSha = (Get-FileHash -LiteralPath $configPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualSha -ne $expectedSha) { throw 'Configuration changed since diagnosis. Recheck before applying.' }
$utf8 = [Text.UTF8Encoding]::new($false, $true)
$hasBom = $originalBytes.Length -ge 3 -and $originalBytes[0] -eq 239 -and $originalBytes[1] -eq 187 -and $originalBytes[2] -eq 191
$offset = 0
if ($hasBom) { $offset = 3 }
$text = $utf8.GetString($originalBytes, $offset, $originalBytes.Length - $offset)
$sectionPattern = '(?ms)(^\[windows\][^\r\n]*\r?\n)(?<body>.*?)(?=^\[|\z)'
$sections = [regex]::Matches($text, $sectionPattern)
if ($sections.Count -ne 1) { throw 'Expected exactly one windows section.' }
$section = $sections[0]
$settingPattern = '(?m)^sandbox[ \t]*=[ \t]*"unelevated"[ \t]*(?:#[^\r\n]*)?\r?$'
if ([regex]::Matches($section.Groups['body'].Value, $settingPattern).Count -ne 1) { throw 'Expected exactly one unelevated sandbox setting.' }
$replacementPattern = '(?m)(^sandbox[ \t]*=[ \t]*")unelevated(")'
$newBody = [regex]::Replace($section.Groups['body'].Value, $replacementPattern, '$1elevated$2')
$newText = $text.Substring(0, $section.Index) + $section.Groups[1].Value + $newBody + $text.Substring($section.Index + $section.Length)
$reversedBody = [regex]::Replace($newBody, '(?m)(^sandbox[ \t]*=[ \t]*")elevated(")', '$1unelevated$2')
if ($reversedBody -cne $section.Groups['body'].Value) { throw 'Unexpected extra configuration edits detected.' }
Write-Output 'Verified change: [windows] sandbox="unelevated" -> sandbox="elevated"'
Write-Output 'Approval policy, filesystem permissions, and network restrictions remain unchanged.'
if (-not $Apply) { Write-Output 'Preview only. Use -Apply to save the verified change.'; exit 0 }
if ((Get-FileHash -LiteralPath $configPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedSha) { throw 'Concurrent configuration change detected.' }
$newBytes = $utf8.GetBytes($newText)
if ($hasBom) { $newBytes = [byte[]](239,187,191) + $newBytes }
$parent = [IO.Path]::GetDirectoryName($configPath)
$identifier = [Guid]::NewGuid().ToString('N')
$tempPath = [IO.Path]::Combine($parent, 'config.sandbox-repair.' + $identifier + '.tmp')
$backupPath = [IO.Path]::Combine($parent, 'config.before-elevated.' + $identifier + '.toml')
[IO.File]::WriteAllBytes($tempPath, $newBytes)
[IO.File]::Replace($tempPath, $configPath, $backupPath)
$written = [IO.File]::ReadAllBytes($configPath)
if ([Convert]::ToBase64String($written) -cne [Convert]::ToBase64String($newBytes)) { throw 'Post-write verification failed. Use the preserved backup.' }
Write-Output ('Configuration updated. Backup: ' + $backupPath)
Write-Output 'Restart the application to load the updated backend. Complete official elevated sandbox setup if prompted.'
