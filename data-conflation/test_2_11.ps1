$config = Get-Content config.local.json | ConvertFrom-Json
$config.agol.password = "WrongPassword123!@#"
$config | ConvertTo-Json | Set-Content config.local.json
