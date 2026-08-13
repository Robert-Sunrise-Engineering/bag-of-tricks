<#
.SYNOPSIS
    Enables GPU-P (GPU Partitioning) on an existing Hyper-V virtual machine.

.DESCRIPTION
    Ports the core "attach a GPU partition to a VM" logic from the archived
    Easy-GPU-PV project (https://github.com/jamesstringer90/Easy-GPU-PV) so
    it can be run against any VM you already have, not just ones that
    project created from scratch.

    Steps performed:
      1. Verifies the caller is a member of "Hyper-V Administrators" (or
         local Administrators) - see NOTES, this cannot be bypassed.
      2. Stops the VM if it's running (required to change MMIO / add a
         partition adapter).
      3. Sets the low/high MMIO space Hyper-V needs to map the GPU
         partition into the VM's address space.
      4. Adds a GPU partition adapter tied to a physical GPU on the host
         and sets its VRAM / encode / decode / compute resource shares.
      5. Enables the Guest Service Interface integration service.
      6. Starts the VM, waits for it to report "IC Heartbeat" is OK, then
         stages the matching GPU driver package into an unprotected temp
         folder in the guest via Copy-VMFile (this only requires Hyper-V
         Administrators on the host). It then runs a command *inside* the
         guest, authenticated with -GuestCredential, that moves those
         staged files into the guest's protected driver store - that final
         privileged write uses the guest's own local admin rights, not
         anything from the host, so it works even without host admin.

.PARAMETER VMName
    Name of an existing Hyper-V VM (must be Generation 2, Windows 10/11 or
    Server 2019+ guest, on a host that supports GPU-P).

.PARAMETER GPUNameFilter
    Optional substring to pick a specific physical GPU when the host has
    more than one. If omitted, the first partitionable GPU is used.

.PARAMETER MinVRAMPercent
    Minimum percentage (1-100) of VRAM, Encode, Decode, and Compute
    resource pools to grant this VM's partition. Defaults to 80, matching
    Easy-GPU-PV's default.

.PARAMETER MaxVRAMPercent
    Maximum percentage (1-100) of VRAM, Encode, Decode, and Compute
    resource pools to grant this VM's partition. Defaults to 100, matching
    Easy-GPU-PV's default.

.PARAMETER OptimalVRAMPercent
    Optimal percentage (1-100) of VRAM, Encode, Decode, and Compute
    resource pools to grant this VM's partition. Defaults to 100, matching
    Easy-GPU-PV's default.

.PARAMETER SkipDriverCopy
    Skip copying host GPU drivers into the guest (use if you've already
    installed/copied matching drivers another way, e.g. guest-side
    Windows Update already grabbed a compatible driver).

.PARAMETER DoNotStartVM
    Leave the VM off after configuring it instead of starting it (driver
    copy will also be skipped, since that requires the VM to be running).

.PARAMETER ForceDriverUpdate
    Overwrite driver folders that already exist in the guest instead of
    skipping them. Only needed after updating the GPU driver on the host.
    Will fail with a file-lock error if the guest's GPU partition is
    actively using the old driver - stop the VM's use of the GPU (or
    disable the device in guest Device Manager) first if that happens.

.PARAMETER GuestCredential
    Credentials for a local administrator account INSIDE the guest VM
    (a Windows login on that VM's OS, not your host account). Required
    for driver copy unless -SkipDriverCopy is used. This is deliberately
    separate from your host permissions - see NOTES.

.EXAMPLE
    .\Enable-GpuP.ps1 -VMName "Workstation01"

.EXAMPLE
    .\Enable-GpuP.ps1 -VMName "Workstation01" -GPUNameFilter "RTX" -SkipDriverCopy

.NOTES
    ADMIN REQUIREMENT - READ THIS FIRST:
    This script relies on TWO separate permission domains that are easy
    to conflate:

    1. HOST permissions - membership in the local "Hyper-V Administrators"
       group (or local Administrators) on the Hyper-V host. This is what
       lets you manage the VM at all: Add-VMGpuPartitionAdapter, Set-VM
       MMIO space, start/stop the VM, and stage files into an unprotected
       guest folder via Copy-VMFile. Enforced by the Hyper-V VM Management
       Service - no script can work around it. (Mount-VHD, notably, does
       NOT fall under this group - it talks to the Virtual Disk Service
       and commonly needs full local admin, which is why this script no
       longer uses that approach.)

    2. GUEST permissions - a local administrator account *inside* the VM's
       own Windows install, supplied via -GuestCredential. Writing driver
       files into the protected driver store under System32 requires
       elevated rights from somewhere, and once host admin is off the
       table, the guest's own admin account is the only remaining source
       of that authority. This is completely independent of your host
       account - it's whatever login you use on that specific VM.

    If your host admin is going away, keep Hyper-V Administrators (that
    covers everything except the driver copy) and make sure you still
    have an admin login on each guest VM you'll run this against.
#>

[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)]
    [string]$VMName,

    [string]$GPUNameFilter,

    [ValidateRange(1,100)] [int]$MinVRAMPercent    = 80,
    [ValidateRange(1,100)] [int]$MaxVRAMPercent    = 100,
    [ValidateRange(1,100)] [int]$OptimalVRAMPercent = 100,

    [switch]$SkipDriverCopy,
    [switch]$DoNotStartVM,
    [switch]$ForceDriverUpdate,

    [PSCredential]$GuestCredential
)

$ErrorActionPreference = 'Stop'
function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }

# Convert a 1-100 percentage into the 0..1,000,000,000 scale these cmdlets use
function ToResourceUnits([int]$percent) { [uint64]([math]::Floor(1000000000 * $percent / 100)) }

# ---------------------------------------------------------------------------
# 0. Permission check
# ---------------------------------------------------------------------------
$identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
$isHyperVAdmin = $principal.IsInRole("Hyper-V Administrators") `
    -or $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isHyperVAdmin) {
    throw "You must be a member of the local 'Hyper-V Administrators' group (or local Administrators) on THIS machine to run this script. See the script's .NOTES for why this can't be worked around."
}

# ---------------------------------------------------------------------------
# 1. Locate the VM
# ---------------------------------------------------------------------------
Write-Step "Looking up VM '$VMName'"
$vm = Get-VM -Name $VMName -ErrorAction Stop

if ($vm.Generation -ne 2) {
    throw "VM '$VMName' is Generation 1. GPU-P requires a Generation 2 VM."
}

# ---------------------------------------------------------------------------
# 2. Pick a partitionable GPU
# ---------------------------------------------------------------------------
Write-Step "Enumerating partitionable GPUs on this host"
$gpus = if (Get-Command Get-VMHostPartitionableGpu -ErrorAction SilentlyContinue) {
    # Current cmdlet (newer Hyper-V builds)
    Get-VMHostPartitionableGpu
} else {
    # Fallback for older hosts where Get-VMPartitionableGpu hasn't been replaced yet
    Get-VMPartitionableGpu
}
if (-not $gpus) {
    throw "No partitionable GPUs found. Confirm GPU-P is supported by this GPU/driver and that the Hyper-V role/host OS supports it."
}

$gpu = if ($GPUNameFilter) {
    $gpus | Where-Object { $_.Name -like "*$GPUNameFilter*" } | Select-Object -First 1
} else {
    $gpus | Select-Object -First 1
}
if (-not $gpu) {
    throw "No partitionable GPU matched filter '$GPUNameFilter'. Available instance paths:`n$($gpus.Name -join "`n")"
}
Write-Host "  Using GPU instance path: $($gpu.Name)"

# ---------------------------------------------------------------------------
# 3. Stop the VM (required to change MMIO space / add the adapter)
# ---------------------------------------------------------------------------
if ($vm.State -ne 'Off') {
    Write-Step "Stopping VM '$VMName' (required for GPU-P configuration)"
    Stop-VM -VM $vm -Force
    $vm = Get-VM -Name $VMName
}

# ---------------------------------------------------------------------------
# 4. MMIO space - Hyper-V needs a large address window to map the GPU
#    partition's memory into the VM.
# ---------------------------------------------------------------------------
Write-Step "Setting low/high MMIO space and guest-controlled cache types"
Set-VM -VM $vm -LowMemoryMappedIoSpace 3GB
Set-VM -VM $vm -HighMemoryMappedIoSpace 32GB
Set-VM -VM $vm -GuestControlledCacheTypes $true

# ---------------------------------------------------------------------------
# 5. Remove any existing GPU partition adapter, then add a fresh one
# ---------------------------------------------------------------------------
$existing = Get-VMGpuPartitionAdapter -VM $vm -ErrorAction SilentlyContinue
if ($existing) {
    Write-Step "Removing existing GPU partition adapter(s) before re-adding"
    Remove-VMGpuPartitionAdapter -VM $vm
}

Write-Step "Adding GPU partition adapter"
Add-VMGpuPartitionAdapter -VM $vm -InstancePath $gpu.Name

$minU = ToResourceUnits $MinVRAMPercent
$maxU = ToResourceUnits $MaxVRAMPercent
$optU = ToResourceUnits $OptimalVRAMPercent

Write-Step "Setting partition resource shares (VRAM/Encode/Decode/Compute)"
Set-VMGpuPartitionAdapter -VM $vm `
    -MinPartitionVRAM $minU -MaxPartitionVRAM $maxU -OptimalPartitionVRAM $optU `
    -MinPartitionEncode $minU -MaxPartitionEncode $maxU -OptimalPartitionEncode $optU `
    -MinPartitionDecode $minU -MaxPartitionDecode $maxU -OptimalPartitionDecode $optU `
    -MinPartitionCompute $minU -MaxPartitionCompute $maxU -OptimalPartitionCompute $optU

# ---------------------------------------------------------------------------
# 6. Enable Guest Service Interface (needed for Copy-VMFile staging)
# ---------------------------------------------------------------------------
Write-Step "Enabling Guest Service Interface integration service"
Enable-VMIntegrationService -VM $vm -Name "Guest Service Interface"

# ---------------------------------------------------------------------------
# 7. Start the VM (driver copy needs it running for both Copy-VMFile and
#    PowerShell Direct)
# ---------------------------------------------------------------------------
$skipDriverStep = $SkipDriverCopy -or $DoNotStartVM

if ($DoNotStartVM) {
    Write-Host "DoNotStartVM specified - leaving VM off. Driver copy skipped." -ForegroundColor Yellow
} else {
    Write-Step "Starting VM '$VMName'"
    Start-VM -VM $vm

    if (-not $SkipDriverCopy) {
        Write-Step "Waiting for VM heartbeat (up to 3 minutes)"
        $deadline = (Get-Date).AddMinutes(3)
        do {
            Start-Sleep -Seconds 5
            $hb = (Get-VMIntegrationService -VM $vm -Name "Heartbeat").PrimaryStatusDescription
        } until ($hb -eq 'OK' -or (Get-Date) -gt $deadline)

        if ($hb -ne 'OK') {
            Write-Warning "VM did not report a healthy heartbeat in time. Skipping driver copy - rerun once the VM is fully booted."
            $skipDriverStep = $true
        }
    }
}

# ---------------------------------------------------------------------------
# 8. Driver copy: stage via Copy-VMFile (unprotected folder, needs only
#    host Hyper-V Administrators), then move into the protected driver
#    store from INSIDE the guest via PowerShell Direct, authenticated as
#    a guest local admin (-GuestCredential). That final privileged write
#    relies entirely on the guest's own admin rights, not host permissions
#    - which is what makes this work without host local admin.
# ---------------------------------------------------------------------------
if (-not $skipDriverStep) {
    if (-not $GuestCredential) {
        $GuestCredential = Get-Credential -Message "Local administrator credentials for INSIDE the guest VM '$VMName' (not your host login)"
    }

    Write-Step "Locating host GPU driver files to copy"
    $videoController = $null
    if ($gpu.Name -match 'VEN_[0-9A-F]{4}&DEV_[0-9A-F]{4}') {
        $id = $Matches[0]
        $videoController = Get-CimInstance Win32_VideoController |
            Where-Object { $_.PNPDeviceID -like "*$id*" } |
            Select-Object -First 1
    }
    if (-not $videoController) {
        $videoController = Get-CimInstance Win32_VideoController | Select-Object -First 1
        Write-Warning "Could not match the selected GPU's instance path to a specific Win32_VideoController entry; falling back to the first video controller found. Verify the correct driver gets copied."
    }

    if (-not $videoController -or -not $videoController.InstalledDisplayDrivers) {
        Write-Warning "Could not determine host driver files automatically. Copy a matching driver into the guest manually."
    } else {
        $driverDllPaths = $videoController.InstalledDisplayDrivers.Split(',') | Select-Object -Unique
        $driverDirs = $driverDllPaths | ForEach-Object { Split-Path $_ -Parent } | Select-Object -Unique | Where-Object { Test-Path $_ }
        $driverDirs = $driverDirs | Where-Object {
            if ($_ -match '\\DriverStore\\FileRepository\\') {
                $true
            } else {
                Write-Warning "Skipping '$_' - outside DriverStore\FileRepository; loose driver files like this are not currently copied, see README follow-up"
                $false
            }
        }

        $stagingRoot = "C:\Windows\Temp\GpuPDriverStaging"
        $destRoot = "C:\Windows\System32\HostDriverStore\FileRepository"

        foreach ($dir in $driverDirs) {
            $folderName = Split-Path $dir -Leaf

            $alreadyPresent = (-not $ForceDriverUpdate) -and (Invoke-Command -VMName $VMName -Credential $GuestCredential -ScriptBlock {
                param($DestRoot, $FolderName)
                Test-Path (Join-Path $DestRoot $FolderName)
            } -ArgumentList $destRoot, $folderName)

            if ($alreadyPresent) {
                Write-Host "  '$folderName' already present in guest driver store - skipping (files may be locked/in use by the active partition; use -ForceDriverUpdate to overwrite)." -ForegroundColor Yellow
                continue
            }

            if ($ForceDriverUpdate) {
                if ($PSCmdlet.ShouldProcess($VMName, "Remove existing driver folder '$folderName' in guest driver store")) {
                    Invoke-Command -VMName $VMName -Credential $GuestCredential -ScriptBlock {
                        param($DestRoot, $FolderName)
                        $existing = Join-Path $DestRoot $FolderName
                        if (Test-Path $existing) {
                            Remove-Item -Path $existing -Recurse -Force -ErrorAction Stop
                        }
                    } -ArgumentList $destRoot, $folderName -ErrorAction Stop
                }
            }

            $stagingDest = "$stagingRoot\$folderName"
            Write-Step "Staging driver folder '$folderName' into guest temp folder"
            Get-ChildItem -Path $dir -File -Recurse | ForEach-Object {
                $relative = $_.FullName.Substring($dir.Length).TrimStart('\')
                Copy-VMFile -VM $vm -SourcePath $_.FullName `
                    -DestinationPath "$stagingDest\$relative" `
                    -FileSource Host -CreateFullPath -Force
            }

            Write-Step "Moving staged driver '$folderName' into the guest's driver store"
            if ($PSCmdlet.ShouldProcess($VMName, "Move staged driver '$folderName' into guest driver store")) {
                Invoke-Command -VMName $VMName -Credential $GuestCredential -ScriptBlock {
                    param($StagingRoot, $DestRoot, $FolderName)
                    $src = Join-Path $StagingRoot $FolderName
                    if (-not (Test-Path $src)) { return }
                    $dest = Join-Path $DestRoot $FolderName
                    New-Item -ItemType Directory -Path $dest -Force | Out-Null
                    Copy-Item -Path "$src\*" -Destination $dest -Recurse -Force
                    Remove-Item -Path $src -Recurse -Force -ErrorAction SilentlyContinue
                } -ArgumentList $stagingRoot, $destRoot, $folderName
            }
        }
    }
} else {
    Write-Host "Driver copy skipped." -ForegroundColor Yellow
}

Write-Host "GPU-P configuration complete for '$VMName'." -ForegroundColor Green
Write-Host "Inside the guest, open Device Manager and rescan for hardware changes (or reboot the guest) so it picks up the newly-copied driver." -ForegroundColor Green
