# Enable-GpuP.ps1 — How It Works

A reusable script for enabling GPU-P (GPU Partitioning) on an **existing**
Hyper-V VM, without needing full local admin on the host — only membership
in the **Hyper-V Administrators** group.

It's a from-scratch port of the core idea behind the archived
[Easy-GPU-PV](https://github.com/jamesstringer90/Easy-GPU-PV) project,
adapted so it works on any VM you already have (not just ones a toolkit
created for you), and reworked around a narrower host permission model.

---

## The two permission domains

This is the most important thing to understand before running it.

| | Covers | Required rights |
|---|---|---|
| **Host permissions** | Managing the VM itself: adding the GPU partition adapter, MMIO settings, start/stop, staging files into an *unprotected* guest folder | Hyper-V Administrators (or local Admin) on the **host** |
| **Guest permissions** | Writing driver files into the guest's protected system driver store | A local **admin account inside the VM's own Windows install** |

These are genuinely separate. Losing host local admin (while keeping
Hyper-V Administrators) does not affect the guest side at all — it's a
completely different login, scoped to that one VM.

---

## What the script does, step by step

1. **Checks host permissions.** Confirms you're in Hyper-V Administrators
   (or local Admin) before touching anything. If not, it stops immediately
   with an explanation.

2. **Looks up the VM and picks a GPU.** Uses `Get-VM` to find the target
   VM, and `Get-VMPartitionableGpu` to find a GPU on the host that
   supports partitioning. If the host has more than one, `-GPUNameFilter`
   picks which one by a substring match (e.g. `"RTX"`).

3. **Stops the VM.** Hyper-V won't let you change MMIO space or add a
   partition adapter while a VM is running, so it's powered off first.

4. **Sets MMIO space.** `Set-VM -LowMemoryMappedIoSpace` /
   `-HighMemoryMappedIoSpace` give Hyper-V the address space it needs to
   map the GPU partition's memory into the VM. `GuestControlledCacheTypes`
   is also enabled, which GPU-P requires.

5. **Adds the GPU partition adapter.** Removes any existing partition
   adapter on the VM (clean slate), then `Add-VMGpuPartitionAdapter`
   attaches a partition of the chosen GPU. Resource shares (VRAM, encode,
   decode, compute) are set via `Set-VMGpuPartitionAdapter`, using the
   `-Min/Max/OptimalVRAMPercent` parameters (default: 80% min, 100%
   max/optimal — the same defaults Easy-GPU-PV used).

6. **Enables the Guest Service Interface.** This Hyper-V integration
   service is what lets the host copy files into the guest without any
   network connectivity — it rides the VM's existing host↔guest channel
   (VMBus), not SMB/RDP, and needs no guest login.

7. **Starts the VM and waits for a heartbeat.** Driver staging needs the
   guest actually running and responsive, so the script waits (up to 3
   minutes) for the Heartbeat integration service to report OK.

8. **Stages the driver files.** The script finds the physical GPU's
   driver files on the host (via `Win32_VideoController` →
   `InstalledDisplayDrivers`) and copies them with `Copy-VMFile` into an
   **unprotected** temp folder inside the guest
   (`C:\Windows\Temp\GpuPDriverStaging`). This step only needs host
   Hyper-V Administrators rights — it's not writing anywhere sensitive
   yet.

9. **Moves the drivers into place — from inside the guest.** This is the
   key step that avoids needing host admin. `Invoke-Command -VMName`
   (PowerShell Direct) opens a session *inside* the VM, authenticated with
   the `-GuestCredential` you supply — a local admin account on that VM,
   unrelated to your host login. Running as that guest admin, it moves
   the staged files into the protected
   `C:\Windows\System32\HostDriverStore\FileRepository` folder and cleans
   up the staging directory. Because this write happens under the guest's
   own admin authority, it works regardless of what your host permissions
   are.

10. **Done.** From here, open Device Manager inside the guest and rescan
    for hardware changes (or just reboot the guest) so Windows binds the
    newly-copied driver to the partitioned GPU.

---

## Usage

```powershell
# Basic — will prompt for guest credentials interactively
.\Enable-GpuP.ps1 -VMName "Workstation01"

# Supply guest credentials up front
.\Enable-GpuP.ps1 -VMName "Workstation01" -GuestCredential (Get-Credential)

# Multiple GPUs on the host — pick one by name
.\Enable-GpuP.ps1 -VMName "Workstation01" -GPUNameFilter "RTX"

# Skip driver copy entirely (e.g. guest already has a working driver)
.\Enable-GpuP.ps1 -VMName "Workstation01" -SkipDriverCopy

# Configure GPU-P but leave the VM off (also skips driver copy)
.\Enable-GpuP.ps1 -VMName "Workstation01" -DoNotStartVM
```

### Parameters

| Parameter | Default | Purpose |
|---|---|---|
| `-VMName` | *(required)* | Name of the existing Hyper-V VM |
| `-GPUNameFilter` | first GPU found | Substring to pick a specific host GPU |
| `-MinVRAMPercent` | 80 | Minimum VRAM/encode/decode/compute share |
| `-MaxVRAMPercent` | 100 | Maximum share |
| `-OptimalVRAMPercent` | 100 | Optimal share |
| `-SkipDriverCopy` | off | Skip staging + moving driver files |
| `-DoNotStartVM` | off | Leave the VM off; implies skipping driver copy |
| `-ForceDriverUpdate` | off | Overwrite driver files already present in the guest |
| `-GuestCredential` | prompts if needed | Local admin credentials **inside** the guest |

---

## Why it's built this way (troubleshooting history)

Two approaches were tried and rejected before landing on the
stage-then-move design:

- **Copying drivers straight into the protected path via `Copy-VMFile`**
  failed with `Access is denied (0x80070005)`. This turned out to be a
  known limitation of the Guest Service Interface — even running as
  SYSTEM inside the guest, it's commonly denied write access to the
  protected driver-store path under `System32`. Other people building
  similar tooling hit the same wall.
- **Mounting the VM's VHD offline (`Mount-VHD`) and copying files
  directly** worked functionally, but `Mount-VHD` talks to the Virtual
  Disk Service rather than the Hyper-V VM Management Service — a
  different permission boundary that commonly requires full local admin,
  not just Hyper-V Administrators. Since the whole point of this script
  is to work without host admin, that made it a dead end.

- **Re-running against an already-configured VM** failed with
  `The process cannot access the file ... because it is being used by
  another process` on a driver DLL. This isn't a permissions issue - it's
  Windows refusing to overwrite a driver file that's already loaded and
  actively in use by the working GPU partition from a prior run. Fixed by
  making the driver copy idempotent: each driver folder is skipped if
  it's already present in the guest, rather than blindly re-copied every
  time. Use `-ForceDriverUpdate` if you've updated the driver on the host
  and genuinely need to push the new version in (this will itself fail if
  the old files are still locked - stop the VM's use of the GPU first).

- `Get-VMPartitionableGpu` is deprecated in favor of
  `Get-VMHostPartitionableGpu` on newer Hyper-V builds. The script now
  tries the new cmdlet first and falls back to the old one automatically,
  so it works across host versions without a warning.

### Known limitation: loose vendor API DLLs aren't copied

The driver copy only stages folders under `DriverStore\FileRepository\`
(that filter is what prevents a loose driver-file entry from making the
script recurse-copy the entire guest `System32` tree). Some vendor API
DLLs — e.g. `nvapi64.dll`, `nvml.dll` — live *outside* `FileRepository`
(typically directly in `C:\Windows\System32` on the host). Those are
**not** copied, and the script prints a `Skipping '...' - outside
DriverStore\FileRepository` warning when it encounters them.

Symptom: the GPU partition enumerates fine in guest Device Manager, but
vendor-specific functionality (CUDA, NVENC, the NVIDIA control panel,
etc.) fails. Fix: copy the missing DLLs into the guest's
`C:\Windows\System32` manually (via `Copy-VMFile` into an unprotected
folder, then move them into place with PowerShell Direct as the guest
admin — same stage-then-move pattern the script uses for the driver
folders). The exact file list varies by driver version and wasn't
hard-coded here because it couldn't be verified without real hardware.


Staging via `Copy-VMFile` only needs host permissions; the final move
into the protected driver store happens under the guest's own admin
credential via PowerShell Direct — that split is what keeps working once
host admin goes away, because the one privileged write no longer depends
on host permissions at all.

---

## Making this reusable long-term

A few things worth doing now, while things are working and you still have
both permission levels to verify against:

1. **Test as Hyper-V Administrators only, before your host admin actually
   goes away.** Everything in this script *should* work under just that
   group, but it's only been proven end-to-end while you still had both.
   If your host admin can temporarily pull you from local Administrators
   (keeping you in Hyper-V Administrators), do a dry run against a test
   VM to confirm nothing silently depended on the broader rights.

2. **Give each guest VM's admin account a real password**, not a blank
   one. This isn't just a PSDirect quirk — Windows blocks blank-password
   accounts from anything except console logon by default, so any
   future guest-side automation will hit the same wall. Store that
   password in a password manager per-VM, since it'll likely differ
   from your host login.

3. **Keep the script and this README somewhere durable** — not
   `Downloads`, which people periodically clear out. A dedicated
   `Scripts\GPU-P\` folder (or a personal git repo if you use one) means
   it's still there next time, along with the reasoning in this doc so
   you're not relitigating the same troubleshooting.

4. **Re-running is safe.** The script removes and re-adds the GPU
   partition adapter cleanly each time, so if you ever need to change
   VRAM shares, switch which GPU a VM uses, or just aren't sure whether
   GPU-P is already configured, it's fine to just run it again.

5. **GPU-P config is persistent per-VM** — once set, it survives normal
   VM restarts. You only need to re-run this script if you delete and
   recreate the VM, want to change its resource shares, or move it to
   partition a different physical GPU. A quick way to check current
   state without changing anything:
   ```powershell
   Get-VMGpuPartitionAdapter -VMName "YourVMName"
   ```

6. **If you manage more than one VM this way**, consider keeping a small
   table (even just a text file) of VM name → GPU filter used → guest
   admin account, so you're not rediscovering that per VM each time.

## Requirements

- Host: member of **Hyper-V Administrators**
- Guest: a working **local admin account** on each VM you'll run this
  against
- VM: Generation 2, Windows 10/11 or Server 2019+ guest
- Host: GPU and driver that support GPU-P (`Get-VMPartitionableGpu`
  returns something)
