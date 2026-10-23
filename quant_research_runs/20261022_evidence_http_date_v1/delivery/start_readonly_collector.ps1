$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:ICIM_STATE_DIR = 'D:\动量策略\IC和IM滚动套利\runtime\ic_im_v1_4_r1'
$env:ICIM_EVIDENCE_DIR = 'D:\动量策略\IC和IM滚动套利\data\icim_v14_original_inputs'
$env:ICIM_REQUIRE_MIGRATION = '1'
$capture = Start-Process -FilePath 'C:\Python314\python.exe' -ArgumentList '-X utf8 -B "D:\动量策略\IC和IM滚动套利\capture_ic_im_v1_4_inputs.py" --loop --state-dir "D:\动量策略\IC和IM滚动套利\runtime\ic_im_v1_4_r1" --evidence-dir "D:\动量策略\IC和IM滚动套利\data\icim_v14_original_inputs" --source-pin "D:\动量策略\IC和IM滚动套利\quant_research_runs\20261022_evidence_http_date_v1\delivery\capture_source_pin.json"' -WorkingDirectory 'D:\动量策略\IC和IM滚动套利' -WindowStyle Hidden -PassThru -RedirectStandardOutput 'D:\动量策略\IC和IM滚动套利\quant_research_runs\20261022_evidence_http_date_v1\delivery\collector_stdout.log' -RedirectStandardError 'D:\动量策略\IC和IM滚动套利\quant_research_runs\20261022_evidence_http_date_v1\delivery\collector_stderr.log'
$capture.Id | Set-Content -LiteralPath 'D:\动量策略\IC和IM滚动套利\quant_research_runs\20261022_evidence_http_date_v1\delivery\collector_pid.txt' -Encoding Ascii
[pscustomobject]@{pid=$capture.Id; started_at=(Get-Date).ToString('o'); python='C:\Python314\python.exe'; source_pin='D:\动量策略\IC和IM滚动套利\quant_research_runs\20261022_evidence_http_date_v1\delivery\capture_source_pin.json'; evidence_dir='D:\动量策略\IC和IM滚动套利\data\icim_v14_original_inputs'; state_read_only='D:\动量策略\IC和IM滚动套利\runtime\ic_im_v1_4_r1'} | ConvertTo-Json | Set-Content -LiteralPath 'D:\动量策略\IC和IM滚动套利\quant_research_runs\20261022_evidence_http_date_v1\delivery\launch_receipt.json' -Encoding UTF8
$capture.Id
