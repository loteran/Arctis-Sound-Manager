# Fix : WirePlumber crash causé par linux-arctis-manager

## Symptôme
La session plante aléatoirement. WirePlumber crash avec SIGABRT (core-dump), ce qui entraîne l'arrêt de PipeWire et la mort de la session graphique.

## Cause racine
LAM crée les virtual sinks `Arctis_Game` et `Arctis_Chat` en chargeant des modules via la couche de compatibilité PulseAudio (`pulsectl.module_load`) :
- `module-null-sink` (pour le sink virtuel)
- `module-loopback` (pour router vers le device physique)

Ce chargement déclenche un **use-after-free dans le moteur Lua de WirePlumber 0.5.x** (`libwireplumber-module-lua-scripting.so` → `g_closure_invalidate` → assertion failure → SIGABRT).

Le code concerné est dans `pactl.py`, méthode `create_virtual_sink()` :
```python
self.pulse.module_load('module-null-sink', f'sink_name={name} ...')
self.pulse.module_load('module-loopback', f'source={name}.monitor sink={sink_output} latency_msec=50')
```

## Le fix

`create_virtual_sink()` vérifie déjà si le sink existe avant de charger des modules :
```python
sink = next((s for s in self.get_arctis_sinks(ONLY_VIRTUAL) if s.proplist.get('node.name', '') == name), None)
if sink:
    return  # ← retourne sans charger de modules si le sink existe déjà
```

**Il suffit donc de pré-créer les sinks nativement via PipeWire** pour que LAM ne charge jamais de modules PulseAudio.

## Fichier de config à créer/installer

**Destination finale sur le système de l'utilisateur :**
`~/.config/pipewire/pipewire.conf.d/10-arctis-virtual-sinks.conf`

**Contenu du fichier :**
```conf
# Pre-create Arctis_Game and Arctis_Chat virtual sinks natively via PipeWire.
#
# Without this, linux-arctis-manager creates them by loading PulseAudio
# modules (module-null-sink + module-loopback) via pipewire-pulse, which
# triggers a use-after-free bug in WirePlumber 0.5.x Lua scripting and
# crashes the session.
#
# LAM's create_virtual_sink() checks if the sink already exists (by node.name)
# and returns early if it does — so pre-creating them here prevents any
# PulseAudio module loading entirely.
#
# libpipewire-module-loopback with capture media.class=Audio/Sink is the
# native PipeWire equivalent of PulseAudio's module-null-sink + module-loopback.

context.modules = [
  # Virtual sink: Game channel → physical Arctis device
  {
    name  = libpipewire-module-loopback
    flags = [ nofail ]
    args  = {
      node.description = "Arctis Nova Pro Wireless Game"
      capture.props    = {
        node.name      = "Arctis_Game"
        media.class    = Audio/Sink
        audio.channels = 2
        audio.position = [ FL FR ]
      }
      playback.props   = {
        node.name         = "Arctis_Game_sink_out"
        audio.channels    = 2
        audio.position    = [ FL FR ]
        stream.dont-remix = true
        node.target       = "alsa_output.usb-SteelSeries_Arctis_Nova_Pro_Wireless-00.analog-stereo"
        latency.msec      = 50
      }
    }
  }
  # Virtual sink: Chat channel → physical Arctis device
  {
    name  = libpipewire-module-loopback
    flags = [ nofail ]
    args  = {
      node.description = "Arctis Nova Pro Wireless Chat"
      capture.props    = {
        node.name      = "Arctis_Chat"
        media.class    = Audio/Sink
        audio.channels = 2
        audio.position = [ FL FR ]
      }
      playback.props   = {
        node.name         = "Arctis_Chat_sink_out"
        audio.channels    = 2
        audio.position    = [ FL FR ]
        stream.dont-remix = true
        node.target       = "alsa_output.usb-SteelSeries_Arctis_Nova_Pro_Wireless-00.analog-stereo"
        latency.msec      = 50
      }
    }
  }
]
```

## Comment intégrer dans le repo (à adapter selon la version)

1. **Ajouter le fichier de config** dans `scripts/pipewire/10-arctis-virtual-sinks.conf`

2. **Modifier `scripts/install.sh`** pour copier ce fichier lors de l'installation :
   ```bash
   echo "==> Installing WirePlumber crash fix (native PipeWire virtual sinks)..."
   PIPEWIRE_CONF_DIR="$HOME/.config/pipewire/pipewire.conf.d"
   mkdir -p "$PIPEWIRE_CONF_DIR"
   cp "$REPO_DIR/scripts/pipewire/10-arctis-virtual-sinks.conf" "$PIPEWIRE_CONF_DIR/"
   ```

3. **Créer `docs/wireplumber-crash-fix.md`** pour documenter le bug et le fix.

4. **Ajouter une mention dans le README** (section Troubleshooting ou Known Issues).

## Points d'attention lors de l'adaptation

- Vérifier que les noms de node (`Arctis_Game`, `Arctis_Chat`) correspondent toujours aux constantes `PULSE_MEDIA_NODE_NAME` / `PULSE_CHAT_NODE_NAME` dans `constants.py`
- Vérifier que `create_virtual_sink()` dans `pactl.py` fait toujours le check d'existence en début de méthode (condition sine qua non du fix)
- Le nom du device physique (`alsa_output.usb-SteelSeries_Arctis_Nova_Pro_Wireless-00.analog-stereo`) est spécifique au Nova Pro Wireless — à adapter si d'autres devices sont supportés, ou rendre dynamique
- Si le repo a changé la façon de gérer les virtual sinks, adapter en conséquence
