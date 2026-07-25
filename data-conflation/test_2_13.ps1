$config = Get-Content config.local.json | ConvertFrom-Json
$config.agol.PSObject.Properties.Remove("password")
$config | ConvertTo-Json | Set-Content config.local.json
