$config = Get-Content config.local.json | ConvertFrom-Json
$config.agol.PSObject.Properties.Remove("username")
$config | ConvertTo-Json | Set-Content config.local.json
