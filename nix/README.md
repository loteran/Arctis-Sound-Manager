# Arctis Sound Manager on NixOS

Native Nix packaging for [Arctis Sound Manager](https://github.com/loteran/Arctis-Sound-Manager)
— no distrobox, no `unset QT_PLUGIN_PATH …` dance, and the audio survives reboots
and service restarts.

It builds the **unmodified upstream source** and wraps it so it runs natively:

- **Qt env bleed fixed.** The wrappers bake in the correct nixpkgs `QT_PLUGIN_PATH`
  / `QML2_IMPORT_PATH` (qtbase Wayland+xcb platforms, qtwayland integration,
  qtsvg). A bad inherited `QT_PLUGIN_PATH` from the host can no longer crash
  PySide6 — that's what broke the Arch-in-distrobox setup.
- **libusb / libudev** are on `LD_LIBRARY_PATH` for the pyusb / pyudev backends.
- **pw-\* / wpctl / pgrep / systemctl / curl** are on `PATH` so the runtime EQ
  config generator and the "restart filter-chain" path work.
- The NixOS module installs the **udev rules** (every Arctis PID incl. Nova Pro
  Wireless `0x12e0`/`0x12e5`), the **daemon / media-router / filter-chain**
  systemd user services, and the **LADSPA plugins** the Sonar EQ needs
  (`swh-plugins` → SC4 compressor, plate reverb, gate; `rnnoise-plugin`).

Everything is pinned to nixpkgs **25.11** (`nix/sources.nix` + `flake.lock`), so
it builds reproducibly whether or not your system has flakes enabled.

The package version is read directly from `pyproject.toml` — it always tracks the
upstream release automatically.

---

## Quick start (classic — flakes disabled)

This is the path for a system **without** `nix-command`/`flakes`.

1. Clone the repo somewhere persistent (e.g. inside your nixos config tree):

   ```bash
   git clone https://github.com/loteran/Arctis-Sound-Manager.git /etc/nixos/Arctis-Sound-Manager
   ```

2. In `/etc/nixos/configuration.nix`:

   ```nix
   {
     imports = [ /etc/nixos/Arctis-Sound-Manager/nix/module.nix ];

     # PipeWire is required (it's the audio backend ASM drives).
     services.pipewire = {
       enable = true;
       alsa.enable = true;
       pulse.enable = true;
     };

     services.arctis-sound-manager.enable = true;
   }
   ```

3. Rebuild and replug the headset once (so udev applies the new rules):

   ```bash
   sudo nixos-rebuild switch
   ```

The tray GUI starts on next login (`services.arctis-sound-manager.autostartTray`,
on by default). The daemon creates the `Arctis_Game` / `Arctis_Chat` /
`Arctis_Media` virtual sinks when the headset connects; assign apps to them from
KDE's audio applet or ASM's mixer. Per-channel Sonar EQ and HeSuVi 7.1 spatial
work out of the box.

> **Spatial audio (HeSuVi) first run:** pick an HRIR profile in
> **Settings → Spatial Audio** — that copies one of the 58 bundled profiles to
> `~/.local/share/pipewire/hrir_hesuvi/hrir.wav` (offline, no download needed).
>
> **Do not run `asm-setup` on NixOS.** It's for traditional distros: it copies
> read-only files out of the Nix store into `~/.config` (which fails on re-run
> and can leave configs the daemon can't rewrite). This module + the
> `arctis-manager` daemon already handle udev, services, and every PipeWire
> config, so it isn't needed.

### Just the package, no module

```bash
nix-build /etc/nixos/Arctis-Sound-Manager/nix     # → ./result/bin/asm-gui …
nix-shell /etc/nixos/Arctis-Sound-Manager/nix     # drops the asm-* tools on PATH
```

---

## Flake-based NixOS config (pure eval)

If your system config is itself a flake (e.g. `nixosConfigurations.<host>`), do
**not** `imports = [ /abs/path/nix/module.nix ]` — pure flake evaluation forbids
importing arbitrary absolute paths (`access to absolute path … is forbidden in
pure evaluation mode`). Add this flake as an **input** instead:

```nix
{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";

    arctis-sound-manager = {
      # Local checkout. Or: "github:loteran/Arctis-Sound-Manager?dir=nix"
      url = "git+file:///opt/projects/Arctis-Sound-Manager?dir=nix";
      inputs.nixpkgs.follows = "nixpkgs";   # share one nixpkgs
    };
  };

  outputs = { self, nixpkgs, arctis-sound-manager, ... }: {
    nixosConfigurations.senate = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [
        arctis-sound-manager.nixosModules.default
        {
          services.pipewire.enable = true;
          services.arctis-sound-manager.enable = true;
        }
        # … your other modules …
      ];
    };
  };
}
```

`nixosModules.default` defaults the package to the flake's pinned build.

> **Lockability:** a local `git+file://…` input only locks cleanly when `nix/`
> is committed (an uncommitted/dirty tree works but logs "not writing lock
> file … unlocked input" and re-copies each build). Commit `nix/`, or push and
> use the `github:…?dir=nix` URL, to pin it in your `flake.lock`.

Ad-hoc builds:

```bash
nix build "git+file:///opt/projects/Arctis-Sound-Manager?dir=nix#default"
```

---

## Module options

| Option | Default | Notes |
|---|---|---|
| `services.arctis-sound-manager.enable` | `false` | Master switch. Asserts `services.pipewire.enable`. |
| `…​.package` | `pkgs.callPackage ./nix/package.nix { }` | Override to use your own build. Flake users get the flake-pinned package automatically via `nixosModules.default`. |
| `…​.ladspaPlugins` | `[ pkgs.ladspaPlugins pkgs.rnnoise-plugin ]` | On the filter-chain's `LADSPA_PATH`. |
| `…​.autostartTray` | `true` | Launch `asm-gui --systray` on login. |

What the module configures:

- `environment.systemPackages` → `asm-gui`, `asm-daemon`, `asm-cli`, `asm-router`, `asm-setup`, `asm-diag-dinit`.
- `services.udev.packages` → `91-steelseries-arctis.rules`.
- `systemd.user.services.{arctis-manager,arctis-video-router,filter-chain,arctis-gui}`.
- `systemd.tmpfiles` symlink `/usr/lib/ladspa` → the Sonar LADSPA plugins
  (so ASM's FHS-only dependency checker finds plate_1423 / rnnoise).

The per-user PipeWire configs in `~/.config/pipewire/…` are generated by the
daemon at runtime when the headset connects (and self-healed on startup), so
nothing user-specific is managed declaratively here.

---

## Troubleshooting

```bash
systemctl --user status arctis-manager arctis-video-router filter-chain
journalctl --user -u arctis-manager -f
ARCTIS_LOG_LEVEL=debug systemctl --user restart arctis-manager
asm-daemon --verify-setup          # preflight: device YAMLs, deps, udev, pipewire
```

- **Headset not detected:** confirm the udev rule applied — replug, or
  `udevadm info -a /dev/bus/usb/... | grep uaccess`. The rules need a reboot or
  replug after the first `nixos-rebuild switch`.
- **No virtual sinks:** check `pactl list sinks short | grep -i arctis`; the
  daemon only creates them while the headset is powered on and connected.
- **Sonar EQ silent / no compressor:** verify `filter-chain.service` is running
  and `LADSPA_PATH` resolves — `systemctl --user show filter-chain -p Environment`.

---

## Updating the pin

Bump the revision in **both** `nix/sources.nix` (rev + sha256) and
`nix/flake.nix` (input url), then for flake users `nix flake lock --update-input nixpkgs ./nix`.
Get the classic sha256 with:

```bash
nix-prefetch-url --unpack \
  "https://github.com/NixOS/nixpkgs/archive/<rev>.tar.gz" --name source
```
