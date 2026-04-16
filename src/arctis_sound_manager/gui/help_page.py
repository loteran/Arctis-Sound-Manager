"""
Help page — user manual with EN / FR / ES language selector.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from arctis_sound_manager.gui.components import DividerLine
from arctis_sound_manager.gui.report_dialog import ReportBugDialog
from arctis_sound_manager.gui.theme import (
    ACCENT,
    BG_BUTTON,
    BG_BUTTON_HOVER,
    BG_CARD,
    BG_MAIN,
    BORDER,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)

# ── Help content (all languages) ──────────────────────────────────────────────

HELP_CONTENT: dict[str, dict] = {
    "en": {
        "title": "Help",
        "subtitle": "Arctis Sound Manager — User Manual",
        "lang_label": "Language:",
        "sections": [
            {
                "heading": "Overview",
                "body": (
                    "Arctis Sound Manager is a Linux application for configuring SteelSeries Arctis "
                    "headsets. It communicates with your device via USB HID and exposes settings "
                    "through a graphical interface and a system tray icon.\n\n"
                    "The app runs as a background service (asm-daemon) managed by systemd, and a "
                    "GUI front-end (asm-gui) that connects to it over D-Bus."
                ),
            },
            {
                "heading": "Getting started",
                "body": (
                    "1. Connect your SteelSeries headset or DAC via USB.\n"
                    "2. Launch the app from your application menu or run asm-gui in a terminal.\n"
                    "3. The system tray icon appears automatically. Click it to open the menu.\n"
                    "4. The main window opens immediately on first launch. Closing it does not "
                    "quit the tray — the app keeps running in the background.\n"
                    "5. To open the window again, click the tray icon → Open App, or re-run "
                    "asm-gui from the launcher."
                ),
            },
            {
                "heading": "Home",
                "body": (
                    "The Home page shows the headset status (Online / Offline / Charging, battery "
                    "level for the headset and the DAC charge slot) and the audio mixer.\n\n"
                    "── Profiles bar ──\n"
                    "A profile bar sits next to the \"Enable volume sliders\" toggle. "
                    "Click a profile chip to instantly restore all saved settings. "
                    "Use \"＋ Save current settings\" to create a new profile. "
                    "(See the Profiles section below for details.)\n\n"
                    "── Audio mixer ──\n"
                    "Four vertical channel cards let you control the volume of each virtual audio "
                    "sink independently:\n"
                    "• Game — routes game audio (Arctis_Game sink)\n"
                    "• Chat — routes voice / Discord (Arctis_Chat sink)\n"
                    "• Media — routes browsers and video players (Arctis_Media sink)\n"
                    "• HDMI — routes audio to a physical HDMI output (TV, AV receiver, etc.)\n\n"
                    "── HDMI card ──\n"
                    "Unlike the other three cards which route to virtual Arctis sinks on the DAC, "
                    "the HDMI card targets a real HDMI audio device connected to your system "
                    "(sink name contains 'hdmi-surround').\n\n"
                    "To configure it:\n"
                    "1. Make sure your HDMI output is active in your system audio settings.\n"
                    "2. Set the HDMI device as a non-default output — Arctis Sound Manager will keep "
                    "Arctis_Game as the default and route specific apps to HDMI manually.\n"
                    "3. Use the G / C / M / H buttons on an application pill to move that "
                    "stream: H sends it to HDMI, G/C/M send it back to an Arctis sink.\n"
                    "4. Routing choices are saved automatically and restored on the next launch.\n\n"
                    "── Application pills ──\n"
                    "Each card shows which applications are currently playing through it. "
                    "The G / C / M / H buttons on a pill let you instantly re-route that "
                    "application to another channel without leaving the app."
                ),
            },
            {
                "heading": "Profiles",
                "body": (
                    "Profiles let you save and restore your complete audio configuration in one click.\n\n"
                    "── What a profile contains ──\n"
                    "• EQ mode (Sonar or Custom)\n"
                    "• Active EQ preset for each channel (Game / Chat / Micro)\n"
                    "• Macro slider values (Basses / Voix / Aigus) per channel\n"
                    "• Spatial Audio state (enabled, immersion, distance)\n"
                    "• Channel volumes (Game / Chat / Media)\n\n"
                    "── Creating a profile ──\n"
                    "1. Configure your audio settings as desired.\n"
                    "2. Click \"＋ Save current settings\" in the Home page profile bar.\n"
                    "3. Give the profile a name and choose what to include.\n"
                    "4. Click Save — the profile chip appears immediately.\n\n"
                    "── Applying a profile ──\n"
                    "Click any profile chip in the Home page bar, or select it from the "
                    "system tray menu. Settings are applied instantly: volumes are set "
                    "immediately, EQ and PipeWire configs are regenerated and filter-chain "
                    "is restarted in the background.\n\n"
                    "── Deleting a profile ──\n"
                    "Right-click a profile chip → Delete.\n\n"
                    "── Typical use ──\n"
                    "Create a \"Gaming\" profile (Sonar EQ, Spatial Audio on, high Game volume) "
                    "and a \"Work\" profile (Custom EQ, Spatial off, balanced volumes). "
                    "Switch between them in one click from the tray or the Home page."
                ),
            },
            {
                "heading": "Equalizer",
                "body": (
                    "The Equalizer page lets you adjust the frequency response of your headset. "
                    "Two modes are available: Custom and Sonar.\n\n"
                    "── Custom mode ──\n"
                    "A 10-band equalizer (31 Hz → 16 kHz). Drag a slider up to boost, down to cut. "
                    "Changes are sent to the device immediately. You can save and load presets.\n\n"
                    "── Sonar mode ──\n"
                    "Switches to the full Sonar EQ system powered by PipeWire filter-chain "
                    "(see the Sonar EQ section below)."
                ),
            },
            {
                "heading": "Sonar EQ",
                "body": (
                    "The Sonar EQ page provides a full SteelSeries Sonar-style parametric EQ "
                    "system with three independent channels: Game, Chat and Micro.\n\n"
                    "── EQ curve ──\n"
                    "Each channel has an interactive parametric EQ with up to 10 bands. "
                    "Click the curve to add a band, drag to adjust frequency and gain, "
                    "scroll to change Q (bandwidth).\n\n"
                    "── Presets ──\n"
                    "297 Game presets, 8 Chat and 14 Mic presets imported from SteelSeries Sonar. "
                    "Use the search bar to filter, and mark up to 9 favorites for quick access.\n\n"
                    "── Macro sliders ──\n"
                    "Three quick-adjust sliders below the curve: Basses, Voix (Mids) and Aigus "
                    "(Treble), each ±12 dB.\n\n"
                    "── Spatial Audio (Game only) ──\n"
                    "Routes the Game channel through HeSuVi virtual 7.1 surround. "
                    "Immersion (0–12 dB gain) and Distance (plate reverb) sliders let you "
                    "fine-tune the spatial effect.\n\n"
                    "── Volume Boost ──\n"
                    "Adds up to +12 dB gain at the end of the filter chain.\n\n"
                    "── Smart Volume ──\n"
                    "Dynamic compressor with three profiles (Quiet / Balanced / Loud) to "
                    "even out volume differences between sources.\n\n"
                    "All changes are applied live via PipeWire filter-chain (biquad nodes)."
                ),
            },
            {
                "heading": "Micro Processing",
                "body": (
                    "The Micro tab in Sonar EQ includes audio processing features for your "
                    "microphone, applied in real time via PipeWire filter-chain.\n\n"
                    "── ClearCast AI Noise Cancellation ──\n"
                    "Uses rnnoise (neural network) to isolate your voice and remove "
                    "background noise in real time. The slider adjusts the VAD (Voice Activity "
                    "Detection) threshold sensitivity.\n"
                    "Requires: noise-suppression-for-voice package.\n\n"
                    "── Noise Reduction ──\n"
                    "• Background — high-pass filter that cuts low-frequency rumble "
                    "(fans, air conditioning). Slider adjusts the cutoff frequency.\n"
                    "• Impact — high-shelf filter that softens transient noises "
                    "(keyboard clicks, impacts). Slider adjusts attenuation intensity.\n\n"
                    "── Noise Gate ──\n"
                    "Cuts audio below a dB threshold — silences the mic when you are not "
                    "speaking. Adjustable threshold from -60 to -10 dB. "
                    "Auto mode lets the system calculate the optimal threshold.\n"
                    "Requires: swh-plugins package.\n\n"
                    "── Compressor ──\n"
                    "Volume stabilizer that evens out loud and quiet passages. "
                    "The slider controls compression intensity (threshold, ratio and makeup gain). "
                    "Useful for keeping a consistent mic level in voice chat.\n"
                    "Requires: swh-plugins package.\n\n"
                    "Processing chain order: EQ → Boost → Noise Reduction → Noise Gate → "
                    "ClearCast → Compressor."
                ),
            },
            {
                "heading": "ANC / Transparent mode",
                "body": (
                    "The ANC widget on the Headset page lets you control Active Noise Cancelling "
                    "directly from the GUI.\n\n"
                    "Three modes are available:\n"
                    "• Off — noise cancelling disabled\n"
                    "• Transparent — lets outside sound through (adjustable level 10–100%)\n"
                    "• ANC — full active noise cancelling\n\n"
                    "Changes made from the headset buttons are reflected in real time in the GUI, "
                    "and vice-versa."
                ),
            },
            {
                "heading": "Headset / DAC Infos",
                "body": (
                    "This page displays technical information about the connected device:\n\n"
                    "• Device name and model\n"
                    "• USB Vendor ID and Product ID\n"
                    "• Battery level and charging status\n"
                    "• ANC / Transparent mode state and controls\n\n"
                    "This information is useful for troubleshooting or reporting issues."
                ),
            },
            {
                "heading": "Settings",
                "body": (
                    "The Settings page contains all configurable parameters for your device.\n\n"
                    "Depending on your headset model, available options may include:\n"
                    "• Side tone level (microphone monitoring in the headset)\n"
                    "• Chat mix balance\n"
                    "• Sleep timer\n"
                    "• LED brightness\n"
                    "• Wireless transmitter power\n"
                    "• Redirect audio on disconnect (choose fallback output device)\n\n"
                    "Each setting is applied to the device as soon as you change it.\n\n"
                    "── General settings ──\n"
                    "• Launch at startup — enables/disables the system tray autostart via systemd. "
                    "When enabled, the tray icon appears automatically after login.\n"
                    "• Language — switch the interface language (EN / FR / ES).\n\n"
                    "── Check for updates ──\n"
                    "The \"Check for updates\" button at the bottom of the page forces an immediate "
                    "check against the latest GitHub release, bypassing the normal 24-hour cache. "
                    "If a newer version is available, click the result label to open the update "
                    "dialog: for package manager installs (pacman, dnf, apt) it shows the command "
                    "and can open a terminal automatically; for pipx/pip installs it downloads and "
                    "installs the wheel in-app."
                ),
            },
            {
                "heading": "System tray",
                "body": (
                    "The tray icon provides quick access without opening the full window.\n\n"
                    "• Left-click or right-click the icon to open the context menu.\n"
                    "• The menu shows the current headset status (battery, connection state).\n"
                    "• Open App — brings the main window to the foreground.\n"
                    "• Exit — fully quits the application (tray and daemon).\n\n"
                    "If you close the main window, the tray stays active. The app only stops "
                    "when you choose Exit from the tray menu."
                ),
            },
            {
                "heading": "Surround sound",
                "body": (
                    "Arctis Sound Manager supports virtual 7.1 surround sound through PipeWire "
                    "and HeSuVi-compatible HRTF convolution filters.\n\n"
                    "── How it works ──\n"
                    "A virtual 7.1 sink is created in PipeWire. Audio routed to it is processed "
                    "by HRTF convolution (HeSuVi) and folded down to stereo for your headset.\n\n"
                    "── Setup ──\n"
                    "Surround is configured automatically during the initial setup (asm-setup). "
                    "A 'Virtual Surround Sink' will appear in your audio settings once setup "
                    "is complete.\n\n"
                    "── Spatial Audio (Sonar) ──\n"
                    "When using Sonar EQ mode, the Game channel can route through HeSuVi "
                    "automatically via the Spatial Audio toggle. This provides integrated "
                    "surround with Immersion and Distance controls — no manual setup needed.\n\n"
                    "── Custom HRIR ──\n"
                    "Replace ~/.local/share/pipewire/hrir_hesuvi/hrir.wav with any "
                    "14-channel HeSuVi-compatible WAV, then restart:\n"
                    "   systemctl --user restart filter-chain.service"
                ),
            },
            {
                "heading": "Autostart at login",
                "body": (
                    "To start Arctis Sound Manager automatically when you log in, use the "
                    "\"Launch at startup\" toggle in the Settings page.\n\n"
                    "When enabled, the system tray icon (asm-gui --systray) will start "
                    "automatically at login via systemd (arctis-gui.service). The daemon "
                    "(arctis-manager.service) is always started regardless of this toggle.\n\n"
                    "To disable autostart, switch the toggle off."
                ),
            },
            {
                "heading": "Troubleshooting",
                "body": (
                    "Device not detected:\n"
                    "• Make sure udev rules are installed: run asm-cli udev write-rules --reload\n"
                    "• Reconnect the USB cable.\n"
                    "• Check that the daemon is running: "
                    "systemctl --user status arctis-manager.service\n\n"
                    "Settings not applied:\n"
                    "• Restart the daemon: "
                    "systemctl --user restart arctis-manager.service\n\n"
                    "GUI does not open:\n"
                    "• Run asm-gui -vvvv in a terminal to see detailed logs.\n"
                    "• Check that the daemon is running (see above).\n\n"
                    "System tray not appearing at login:\n"
                    "• Enable \"Launch at startup\" in Settings, or check the service:\n"
                    "  systemctl --user status arctis-gui.service"
                ),
            },
        ],
    },
    "fr": {
        "title": "Aide",
        "subtitle": "Arctis Sound Manager — Manuel d'utilisation",
        "lang_label": "Langue :",
        "sections": [
            {
                "heading": "Présentation",
                "body": (
                    "Arctis Sound Manager est une application Linux permettant de configurer les casques "
                    "SteelSeries Arctis. Elle communique avec l'appareil via USB HID et expose les "
                    "réglages dans une interface graphique et une icône dans la barre système.\n\n"
                    "L'application fonctionne comme un service en arrière-plan (asm-daemon) géré "
                    "par systemd, et une interface graphique (asm-gui) qui s'y connecte via D-Bus."
                ),
            },
            {
                "heading": "Démarrage rapide",
                "body": (
                    "1. Branchez votre casque ou DAC SteelSeries en USB.\n"
                    "2. Lancez l'application depuis le menu ou exécutez asm-gui dans un terminal.\n"
                    "3. L'icône de la barre système apparaît automatiquement. Cliquez dessus pour "
                    "ouvrir le menu.\n"
                    "4. La fenêtre principale s'ouvre au premier lancement. La fermer ne quitte "
                    "pas l'application — elle reste active en arrière-plan.\n"
                    "5. Pour rouvrir la fenêtre, cliquez sur l'icône de tray → Ouvrir, ou "
                    "relancez asm-gui depuis le lanceur."
                ),
            },
            {
                "heading": "Accueil",
                "body": (
                    "La page Accueil affiche le statut du casque (En ligne / Hors ligne / En "
                    "charge, niveau de batterie du casque et du slot de charge du DAC) ainsi que "
                    "le mixeur audio.\n\n"
                    "── Barre de profils ──\n"
                    "Une barre de profils se trouve à côté du toggle \"Activer les sliders de volume\". "
                    "Cliquez sur un chip de profil pour restaurer instantanément tous vos réglages. "
                    "Utilisez \"＋ Save current settings\" pour créer un nouveau profil. "
                    "(Voir la section Profils ci-dessous pour les détails.)\n\n"
                    "── Mixeur audio ──\n"
                    "Quatre cartes de canaux permettent de contrôler indépendamment le volume de "
                    "chaque sortie virtuelle :\n"
                    "• Game — audio des jeux (sink Arctis_Game)\n"
                    "• Chat — voix / Discord (sink Arctis_Chat)\n"
                    "• Media — navigateurs et lecteurs vidéo (sink Arctis_Media)\n"
                    "• HDMI — sortie audio vers un appareil HDMI physique (TV, ampli, etc.)\n\n"
                    "── Carte HDMI ──\n"
                    "Contrairement aux trois autres cartes qui ciblent des sinks Arctis virtuels "
                    "sur le DAC, la carte HDMI cible un appareil audio HDMI réel connecté au "
                    "système (sink dont le nom contient 'hdmi-surround').\n\n"
                    "Configuration :\n"
                    "1. Vérifiez que votre sortie HDMI est active dans les paramètres audio système.\n"
                    "2. Ne définissez pas la sortie HDMI comme sortie par défaut — Arctis Sound Manager "
                    "conserve Arctis_Game par défaut et route manuellement les apps vers HDMI.\n"
                    "3. Utilisez les boutons G / C / M / H sur un tag d'application pour changer "
                    "sa destination : H envoie vers HDMI, G/C/M ramènent vers un sink Arctis.\n"
                    "4. Les choix de routage sont sauvegardés et restaurés automatiquement.\n\n"
                    "── Tags d'application ──\n"
                    "Chaque carte affiche les applications qui jouent du son à travers elle. "
                    "Les boutons G / C / M / H permettent de reroutage instantané."
                ),
            },
            {
                "heading": "Profils",
                "body": (
                    "Les profils permettent de sauvegarder et restaurer toute votre configuration "
                    "audio en un seul clic.\n\n"
                    "── Contenu d'un profil ──\n"
                    "• Mode EQ (Sonar ou Personnalisé)\n"
                    "• Preset EQ actif par canal (Game / Chat / Micro)\n"
                    "• Valeurs des macro sliders (Basses / Voix / Aigus) par canal\n"
                    "• État du Spatial Audio (activé, immersion, distance)\n"
                    "• Volumes des canaux (Game / Chat / Media)\n\n"
                    "── Créer un profil ──\n"
                    "1. Configurez vos réglages audio comme souhaité.\n"
                    "2. Cliquez sur \"＋ Save current settings\" dans la barre de profils (page Accueil).\n"
                    "3. Donnez un nom au profil et choisissez ce à inclure.\n"
                    "4. Cliquez Enregistrer — le chip de profil apparaît immédiatement.\n\n"
                    "── Appliquer un profil ──\n"
                    "Cliquez sur un chip dans la barre de profils ou sélectionnez-le depuis le "
                    "menu du tray. Les réglages sont appliqués instantanément : les volumes sont "
                    "mis à jour immédiatement, l'EQ et les configs PipeWire sont régénérés et "
                    "le filter-chain est redémarré en arrière-plan.\n\n"
                    "── Supprimer un profil ──\n"
                    "Clic droit sur un chip → Supprimer.\n\n"
                    "── Exemple d'utilisation ──\n"
                    "Créez un profil \"Gaming\" (Sonar EQ, Spatial Audio activé, volume Game élevé) "
                    "et un profil \"Travail\" (EQ Personnalisé, Spatial off, volumes équilibrés). "
                    "Passez de l'un à l'autre en un clic depuis le tray ou la page Accueil."
                ),
            },
            {
                "heading": "Égaliseur",
                "body": (
                    "La page Égaliseur permet d'ajuster la réponse en fréquence du casque. "
                    "Deux modes sont disponibles : Personnalisé et Sonar.\n\n"
                    "── Mode Personnalisé ──\n"
                    "Égaliseur 10 bandes (31 Hz → 16 kHz). Montez un slider pour amplifier, "
                    "descendez pour atténuer. Sauvegarde et chargement de presets disponibles.\n\n"
                    "── Mode Sonar ──\n"
                    "Bascule vers le système Sonar EQ complet via PipeWire filter-chain "
                    "(voir la section Sonar EQ ci-dessous)."
                ),
            },
            {
                "heading": "Sonar EQ",
                "body": (
                    "La page Sonar EQ offre un système d'égalisation paramétrique complet de type "
                    "SteelSeries Sonar avec trois canaux indépendants : Game, Chat et Micro.\n\n"
                    "── Courbe EQ ──\n"
                    "Chaque canal dispose d'un EQ paramétrique interactif avec jusqu'à 10 bandes. "
                    "Cliquez sur la courbe pour ajouter une bande, glissez pour ajuster fréquence "
                    "et gain, scrollez pour modifier le Q (largeur de bande).\n\n"
                    "── Presets ──\n"
                    "297 presets Game, 8 Chat et 14 Micro importés de SteelSeries Sonar. "
                    "Barre de recherche et jusqu'à 9 favoris en accès rapide.\n\n"
                    "── Macro sliders ──\n"
                    "Trois curseurs rapides sous la courbe : Basses, Voix et Aigus, "
                    "chacun ±12 dB.\n\n"
                    "── Spatial Audio (Game uniquement) ──\n"
                    "Route le canal Game via le surround virtuel 7.1 HeSuVi. "
                    "Les curseurs Immersion (0–12 dB) et Distance (réverbération) permettent "
                    "d'affiner l'effet spatial.\n\n"
                    "── Boost de Volume ──\n"
                    "Ajoute jusqu'à +12 dB de gain en fin de chaîne.\n\n"
                    "── Smart Volume ──\n"
                    "Compresseur dynamique avec trois profils (Silencieux / Équilibré / Fort) "
                    "pour uniformiser les différences de volume entre les sources.\n\n"
                    "Toutes les modifications sont appliquées en direct via PipeWire."
                ),
            },
            {
                "heading": "Traitement Micro",
                "body": (
                    "L'onglet Micro dans Sonar EQ inclut des fonctionnalités de traitement "
                    "audio pour votre microphone, appliquées en temps réel via PipeWire.\n\n"
                    "── ClearCast AI Noise Cancellation ──\n"
                    "Utilise rnnoise (réseau neuronal) pour isoler votre voix et supprimer "
                    "le bruit de fond en temps réel. Le curseur ajuste la sensibilité du "
                    "seuil de détection vocale (VAD).\n"
                    "Nécessite : paquet noise-suppression-for-voice.\n\n"
                    "── Noise Reduction ──\n"
                    "• Background — filtre passe-haut qui coupe les bruits basse fréquence "
                    "(ventilateurs, climatisation). Le curseur ajuste la fréquence de coupure.\n"
                    "• Impact — filtre high-shelf qui atténue les bruits transitoires "
                    "(clics de clavier, impacts). Le curseur ajuste l'intensité.\n\n"
                    "── Noise Gate ──\n"
                    "Coupe l'audio sous un seuil en dB — rend le micro silencieux quand vous ne "
                    "parlez pas. Seuil réglable de -60 à -10 dB. "
                    "Le mode Auto calcule le seuil optimal.\n"
                    "Nécessite : paquet swh-plugins.\n\n"
                    "── Compresseur ──\n"
                    "Stabilisateur de volume qui uniformise les passages forts et faibles. "
                    "Le curseur contrôle l'intensité de la compression. "
                    "Utile pour garder un niveau micro constant en chat vocal.\n"
                    "Nécessite : paquet swh-plugins.\n\n"
                    "Ordre de la chaîne : EQ → Boost → Noise Reduction → Noise Gate → "
                    "ClearCast → Compresseur."
                ),
            },
            {
                "heading": "ANC / Mode Transparent",
                "body": (
                    "Le widget ANC sur la page Casque permet de contrôler la réduction de bruit "
                    "active directement depuis l'interface.\n\n"
                    "Trois modes sont disponibles :\n"
                    "• Off — réduction de bruit désactivée\n"
                    "• Transparent — laisse passer le son extérieur (niveau réglable 10–100%)\n"
                    "• ANC — réduction de bruit active complète\n\n"
                    "Les changements faits depuis les boutons du casque sont reflétés en temps "
                    "réel dans l'interface, et vice-versa."
                ),
            },
            {
                "heading": "Casque / DAC — Infos",
                "body": (
                    "Cette page affiche des informations techniques sur l'appareil connecté :\n\n"
                    "• Nom et modèle de l'appareil\n"
                    "• Identifiant USB (Vendor ID et Product ID)\n"
                    "• Niveau de batterie et état de charge\n"
                    "• Contrôles ANC / Mode Transparent\n\n"
                    "Ces informations sont utiles pour le dépannage ou pour signaler un problème."
                ),
            },
            {
                "heading": "Paramètres",
                "body": (
                    "La page Paramètres regroupe tous les réglages configurables de l'appareil.\n\n"
                    "Selon le modèle de casque, les options disponibles peuvent inclure :\n"
                    "• Niveau de retour micro (sidetone)\n"
                    "• Balance Chat Mix\n"
                    "• Minuterie de mise en veille\n"
                    "• Luminosité des LED\n"
                    "• Puissance du transmetteur sans fil\n"
                    "• Redirection audio en cas de déconnexion (choix du périphérique de secours)\n\n"
                    "Chaque paramètre est appliqué à l'appareil dès sa modification.\n\n"
                    "── Paramètres généraux ──\n"
                    "• Lancer au démarrage — active/désactive le démarrage automatique du tray "
                    "via systemd. Quand activé, l'icône apparaît automatiquement après connexion.\n"
                    "• Langue — change la langue de l'interface (EN / FR / ES).\n\n"
                    "── Rechercher une mise à jour ──\n"
                    "Le bouton en bas de page force une vérification immédiate auprès de GitHub, "
                    "en ignorant le cache habituel de 24h. Si une nouvelle version est disponible, "
                    "cliquez sur le label pour ouvrir le dialog de mise à jour : pour les installs "
                    "via gestionnaire de paquets (pacman, dnf, apt) il affiche la commande et peut "
                    "ouvrir un terminal automatiquement ; pour pipx/pip il télécharge et installe "
                    "le wheel directement dans l'application."
                ),
            },
            {
                "heading": "Barre système (tray)",
                "body": (
                    "L'icône de la barre système permet un accès rapide sans ouvrir la fenêtre.\n\n"
                    "• Clic gauche ou droit sur l'icône pour ouvrir le menu contextuel.\n"
                    "• Le menu affiche le statut actuel du casque (batterie, connexion).\n"
                    "• Ouvrir — amène la fenêtre principale au premier plan.\n"
                    "• Quitter — ferme complètement l'application.\n\n"
                    "Fermer la fenêtre principale laisse le tray actif. L'application ne s'arrête "
                    "que si vous choisissez Quitter depuis le menu du tray."
                ),
            },
            {
                "heading": "Son surround",
                "body": (
                    "Arctis Sound Manager prend en charge le son surround virtuel 7.1 via PipeWire "
                    "et des filtres de convolution HRTF compatibles HeSuVi.\n\n"
                    "── Fonctionnement ──\n"
                    "Un sink 7.1 virtuel est créé dans PipeWire. Le son est traité par convolution "
                    "HRTF (HeSuVi) puis réduit en stéréo pour le casque.\n\n"
                    "── Mise en place ──\n"
                    "Le surround est configuré automatiquement lors de l'installation initiale "
                    "(asm-setup). Un sink 'Virtual Surround Sink' apparaît dans vos paramètres "
                    "audio une fois le setup terminé.\n\n"
                    "── Spatial Audio (Sonar) ──\n"
                    "En mode Sonar EQ, le canal Game peut router automatiquement via HeSuVi "
                    "grâce au toggle Spatial Audio. Cela fournit un surround intégré avec les "
                    "contrôles Immersion et Distance — sans configuration manuelle.\n\n"
                    "── HRIR personnalisé ──\n"
                    "Remplacez ~/.local/share/pipewire/hrir_hesuvi/hrir.wav par tout WAV "
                    "HeSuVi 14 canaux, puis redémarrez :\n"
                    "   systemctl --user restart filter-chain.service"
                ),
            },
            {
                "heading": "Démarrage automatique",
                "body": (
                    "Pour démarrer Arctis Sound Manager automatiquement à la connexion, "
                    "activez le toggle \"Lancer au démarrage\" dans la page Paramètres.\n\n"
                    "Une fois activé, l'icône du tray (asm-gui --systray) démarre "
                    "automatiquement via systemd (arctis-gui.service). Le daemon "
                    "(arctis-manager.service) démarre toujours indépendamment de ce réglage.\n\n"
                    "Pour désactiver le démarrage automatique, désactivez le toggle."
                ),
            },
            {
                "heading": "Dépannage",
                "body": (
                    "Appareil non détecté :\n"
                    "• Vérifiez que les règles udev sont installées : "
                    "asm-cli udev write-rules --reload\n"
                    "• Rebranchez le câble USB.\n"
                    "• Vérifiez que le daemon fonctionne : "
                    "systemctl --user status arctis-manager.service\n\n"
                    "Paramètres non appliqués :\n"
                    "• Redémarrez le daemon : "
                    "systemctl --user restart arctis-manager.service\n\n"
                    "Interface qui ne s'ouvre pas :\n"
                    "• Lancez asm-gui -vvvv dans un terminal pour voir les logs détaillés.\n"
                    "• Vérifiez que le daemon fonctionne (voir ci-dessus).\n\n"
                    "Tray absent au démarrage :\n"
                    "• Activez \"Lancer au démarrage\" dans Paramètres, ou vérifiez :\n"
                    "  systemctl --user status arctis-gui.service"
                ),
            },
        ],
    },
    "es": {
        "title": "Ayuda",
        "subtitle": "Arctis Sound Manager — Manual de usuario",
        "lang_label": "Idioma:",
        "sections": [
            {
                "heading": "Descripción general",
                "body": (
                    "Arctis Sound Manager es una aplicación de Linux para configurar auriculares "
                    "SteelSeries Arctis. Se comunica con el dispositivo a través de USB HID y "
                    "expone los ajustes en una interfaz gráfica y un icono en la bandeja del "
                    "sistema.\n\n"
                    "La aplicación se ejecuta como un servicio en segundo plano (asm-daemon) "
                    "administrado por systemd, y una interfaz gráfica (asm-gui) que se conecta "
                    "a él a través de D-Bus."
                ),
            },
            {
                "heading": "Inicio rápido",
                "body": (
                    "1. Conecta tus auriculares o DAC SteelSeries por USB.\n"
                    "2. Lanza la aplicación desde el menú o ejecuta asm-gui en una terminal.\n"
                    "3. El icono de bandeja aparece automáticamente. Haz clic para abrir el menú.\n"
                    "4. La ventana principal se abre al primer lanzamiento. Cerrarla no cierra la "
                    "aplicación — continúa ejecutándose en segundo plano.\n"
                    "5. Para volver a abrir la ventana, haz clic en el icono de bandeja → Abrir, "
                    "o vuelve a ejecutar asm-gui desde el lanzador."
                ),
            },
            {
                "heading": "Inicio",
                "body": (
                    "La página de Inicio muestra el estado del auricular (En línea / Sin conexión "
                    "/ Cargando, nivel de batería del auricular y del slot de carga del DAC) y el "
                    "mezclador de audio.\n\n"
                    "── Barra de perfiles ──\n"
                    "Una barra de perfiles se encuentra junto al interruptor \"Activar sliders de volumen\". "
                    "Haz clic en un chip de perfil para restaurar al instante toda tu configuración. "
                    "Usa \"＋ Save current settings\" para crear un nuevo perfil. "
                    "(Ver la sección Perfiles para más detalles.)\n\n"
                    "── Mezclador de audio ──\n"
                    "Cuatro tarjetas de canal permiten controlar el volumen de cada salida virtual "
                    "de forma independiente:\n"
                    "• Game — audio de juegos (sink Arctis_Game)\n"
                    "• Chat — voz / Discord (sink Arctis_Chat)\n"
                    "• Media — navegadores y reproductores de vídeo (sink Arctis_Media)\n"
                    "• HDMI — salida de audio a un dispositivo HDMI físico (TV, amplificador, etc.)\n\n"
                    "── Tarjeta HDMI ──\n"
                    "A diferencia de las otras tres tarjetas, que apuntan a sinks Arctis virtuales "
                    "en el DAC, la tarjeta HDMI apunta a un dispositivo de audio HDMI real "
                    "conectado al sistema (sink cuyo nombre contiene 'hdmi-surround').\n\n"
                    "Configuración:\n"
                    "1. Asegúrate de que la salida HDMI esté activa en los ajustes de audio del "
                    "sistema.\n"
                    "2. No establezcas la salida HDMI como predeterminada — Arctis Sound Manager "
                    "mantiene Arctis_Game como predeterminada y enruta manualmente las apps a HDMI.\n"
                    "3. Usa los botones G / C / M / H en una pastilla de aplicación para cambiar "
                    "su destino: H envía a HDMI, G/C/M devuelven a un sink Arctis.\n"
                    "4. Las elecciones de enrutamiento se guardan y restauran automáticamente.\n\n"
                    "── Pastillas de aplicación ──\n"
                    "Cada tarjeta muestra las aplicaciones que reproducen audio a través de ella. "
                    "Los botones G / C / M / H permiten reenrutar al instante."
                ),
            },
            {
                "heading": "Perfiles",
                "body": (
                    "Los perfiles permiten guardar y restaurar toda tu configuración de audio "
                    "con un solo clic.\n\n"
                    "── Contenido de un perfil ──\n"
                    "• Modo EQ (Sonar o Personalizado)\n"
                    "• Preset EQ activo por canal (Game / Chat / Micro)\n"
                    "• Valores de macro sliders (Graves / Voces / Agudos) por canal\n"
                    "• Estado del Spatial Audio (activado, inmersión, distancia)\n"
                    "• Volúmenes de canales (Game / Chat / Media)\n\n"
                    "── Crear un perfil ──\n"
                    "1. Configura tus ajustes de audio como desees.\n"
                    "2. Haz clic en \"＋ Save current settings\" en la barra de perfiles (página Inicio).\n"
                    "3. Asigna un nombre al perfil y elige qué incluir.\n"
                    "4. Haz clic en Guardar — el chip de perfil aparece de inmediato.\n\n"
                    "── Aplicar un perfil ──\n"
                    "Haz clic en un chip en la barra de perfiles o selecciónalo desde el menú "
                    "de la bandeja. Los ajustes se aplican al instante: los volúmenes se actualizan "
                    "inmediatamente, el EQ y las configuraciones de PipeWire se regeneran y el "
                    "filter-chain se reinicia en segundo plano.\n\n"
                    "── Eliminar un perfil ──\n"
                    "Clic derecho en un chip → Eliminar.\n\n"
                    "── Ejemplo de uso ──\n"
                    "Crea un perfil \"Gaming\" (Sonar EQ, Spatial Audio activado, volumen Game alto) "
                    "y un perfil \"Trabajo\" (EQ Personalizado, Spatial off, volúmenes equilibrados). "
                    "Cambia entre ellos con un clic desde la bandeja o la página de Inicio."
                ),
            },
            {
                "heading": "Ecualizador",
                "body": (
                    "La página del Ecualizador permite ajustar la respuesta en frecuencia de los "
                    "auriculares. Dos modos disponibles: Personalizado y Sonar.\n\n"
                    "── Modo Personalizado ──\n"
                    "Ecualizador de 10 bandas (31 Hz → 16 kHz). Sube para realzar, baja para "
                    "atenuar. Permite guardar y cargar presets.\n\n"
                    "── Modo Sonar ──\n"
                    "Cambia al sistema completo Sonar EQ mediante PipeWire filter-chain "
                    "(ver la sección Sonar EQ a continuación)."
                ),
            },
            {
                "heading": "Sonar EQ",
                "body": (
                    "La página Sonar EQ ofrece un sistema de ecualización paramétrica completo "
                    "de tipo SteelSeries Sonar con tres canales independientes: Game, Chat y Micro.\n\n"
                    "── Curva EQ ──\n"
                    "Cada canal dispone de un EQ paramétrico interactivo con hasta 10 bandas. "
                    "Haz clic en la curva para añadir una banda, arrastra para ajustar frecuencia "
                    "y ganancia, desplaza para cambiar el Q (ancho de banda).\n\n"
                    "── Presets ──\n"
                    "297 presets Game, 8 Chat y 14 Micro importados de SteelSeries Sonar. "
                    "Barra de búsqueda y hasta 9 favoritos de acceso rápido.\n\n"
                    "── Macro sliders ──\n"
                    "Tres deslizadores rápidos bajo la curva: Graves, Voces y Agudos, "
                    "cada uno ±12 dB.\n\n"
                    "── Spatial Audio (solo Game) ──\n"
                    "Enruta el canal Game a través del surround virtual 7.1 HeSuVi. "
                    "Los deslizadores Inmersión (0–12 dB) y Distancia (reverberación) permiten "
                    "ajustar el efecto espacial.\n\n"
                    "── Boost de Volumen ──\n"
                    "Añade hasta +12 dB de ganancia al final de la cadena.\n\n"
                    "── Smart Volume ──\n"
                    "Compresor dinámico con tres perfiles (Silencioso / Equilibrado / Alto) "
                    "para uniformizar las diferencias de volumen entre fuentes.\n\n"
                    "Todos los cambios se aplican en directo mediante PipeWire."
                ),
            },
            {
                "heading": "Procesamiento de Micrófono",
                "body": (
                    "La pestaña Micro en Sonar EQ incluye funciones de procesamiento de audio "
                    "para tu micrófono, aplicadas en tiempo real mediante PipeWire.\n\n"
                    "── ClearCast AI Noise Cancellation ──\n"
                    "Usa rnnoise (red neuronal) para aislar tu voz y eliminar el ruido de "
                    "fondo en tiempo real. El deslizador ajusta la sensibilidad del umbral "
                    "de detección vocal (VAD).\n"
                    "Requiere: paquete noise-suppression-for-voice.\n\n"
                    "── Noise Reduction ──\n"
                    "• Background — filtro pasa-altos que corta ruidos de baja frecuencia "
                    "(ventiladores, aire acondicionado). El deslizador ajusta la frecuencia.\n"
                    "• Impact — filtro high-shelf que atenúa ruidos transitorios "
                    "(clics de teclado, impactos). El deslizador ajusta la intensidad.\n\n"
                    "── Noise Gate ──\n"
                    "Corta el audio por debajo de un umbral en dB — silencia el micro cuando "
                    "no hablas. Umbral ajustable de -60 a -10 dB. "
                    "El modo Auto calcula el umbral óptimo.\n"
                    "Requiere: paquete swh-plugins.\n\n"
                    "── Compresor ──\n"
                    "Estabilizador de volumen que uniformiza pasajes fuertes y suaves. "
                    "El deslizador controla la intensidad de la compresión. "
                    "Útil para mantener un nivel de micro constante en chat de voz.\n"
                    "Requiere: paquete swh-plugins.\n\n"
                    "Orden de la cadena: EQ → Boost → Noise Reduction → Noise Gate → "
                    "ClearCast → Compresor."
                ),
            },
            {
                "heading": "ANC / Modo Transparente",
                "body": (
                    "El widget ANC en la página de Auriculares permite controlar la cancelación "
                    "activa de ruido directamente desde la interfaz.\n\n"
                    "Tres modos disponibles:\n"
                    "• Off — cancelación de ruido desactivada\n"
                    "• Transparente — deja pasar el sonido exterior (nivel ajustable 10–100%)\n"
                    "• ANC — cancelación de ruido activa completa\n\n"
                    "Los cambios realizados desde los botones del auricular se reflejan en tiempo "
                    "real en la interfaz, y viceversa."
                ),
            },
            {
                "heading": "Auriculares / DAC — Información",
                "body": (
                    "Esta página muestra información técnica sobre el dispositivo conectado:\n\n"
                    "• Nombre y modelo del dispositivo\n"
                    "• Identificador USB (Vendor ID y Product ID)\n"
                    "• Nivel de batería y estado de carga\n"
                    "• Controles ANC / Modo Transparente\n\n"
                    "Esta información es útil para solucionar problemas o reportar incidencias."
                ),
            },
            {
                "heading": "Ajustes",
                "body": (
                    "La página de Ajustes agrupa todos los parámetros configurables del "
                    "dispositivo.\n\n"
                    "Según el modelo de auriculares, las opciones disponibles pueden incluir:\n"
                    "• Nivel de sidetone (monitoreo del micrófono en el auricular)\n"
                    "• Balance Chat Mix\n"
                    "• Temporizador de suspensión\n"
                    "• Brillo de LEDs\n"
                    "• Potencia del transmisor inalámbrico\n"
                    "• Redirección de audio al desconectar (dispositivo de respaldo)\n\n"
                    "Cada ajuste se aplica al dispositivo en cuanto lo modificas.\n\n"
                    "── Ajustes generales ──\n"
                    "• Iniciar al arranque — activa/desactiva el inicio automático del tray "
                    "mediante systemd. Cuando está activado, el icono aparece tras iniciar sesión.\n"
                    "• Idioma — cambia el idioma de la interfaz (EN / FR / ES).\n\n"
                    "── Buscar actualización ──\n"
                    "El botón al final de la página fuerza una comprobación inmediata en GitHub, "
                    "ignorando la caché habitual de 24h. Si hay una versión más reciente, "
                    "haz clic en el resultado para abrir el diálogo de actualización: para installs "
                    "con gestor de paquetes (pacman, dnf, apt) muestra el comando y puede abrir un "
                    "terminal automáticamente; para pipx/pip descarga e instala el wheel directamente."
                ),
            },
            {
                "heading": "Bandeja del sistema",
                "body": (
                    "El icono de bandeja proporciona acceso rápido sin abrir la ventana "
                    "completa.\n\n"
                    "• Clic izquierdo o derecho en el icono para abrir el menú contextual.\n"
                    "• El menú muestra el estado actual del auricular (batería, conexión).\n"
                    "• Abrir — lleva la ventana principal al primer plano.\n"
                    "• Salir — cierra completamente la aplicación.\n\n"
                    "Cerrar la ventana principal deja activa la bandeja. La aplicación solo se "
                    "detiene si eliges Salir desde el menú de la bandeja."
                ),
            },
            {
                "heading": "Sonido envolvente",
                "body": (
                    "Arctis Sound Manager admite sonido envolvente virtual 7.1 mediante PipeWire "
                    "y filtros de convolución HRTF compatibles con HeSuVi.\n\n"
                    "── Cómo funciona ──\n"
                    "Se crea un sink 7.1 virtual en PipeWire. El audio es procesado por "
                    "convolución HRTF (HeSuVi) y reducido a estéreo para los auriculares.\n\n"
                    "── Configuración ──\n"
                    "El sonido envolvente se configura automáticamente durante la instalación "
                    "inicial (asm-setup). Un sink 'Virtual Surround Sink' aparecerá en tus "
                    "ajustes de audio una vez completado el setup.\n\n"
                    "── Spatial Audio (Sonar) ──\n"
                    "En modo Sonar EQ, el canal Game puede enrutar automáticamente a través de "
                    "HeSuVi mediante el toggle Spatial Audio. Esto proporciona surround integrado "
                    "con controles de Inmersión y Distancia — sin configuración manual.\n\n"
                    "── HRIR personalizado ──\n"
                    "Reemplaza ~/.local/share/pipewire/hrir_hesuvi/hrir.wav con cualquier WAV "
                    "HeSuVi de 14 canales, luego reinicia:\n"
                    "   systemctl --user restart filter-chain.service"
                ),
            },
            {
                "heading": "Inicio automático",
                "body": (
                    "Para iniciar Arctis Sound Manager automáticamente al iniciar sesión, "
                    "activa el interruptor \"Iniciar al arranque\" en la página de Ajustes.\n\n"
                    "Una vez activado, el icono de bandeja (asm-gui --systray) se inicia "
                    "automáticamente mediante systemd (arctis-gui.service). El daemon "
                    "(arctis-manager.service) siempre se inicia independientemente de este ajuste.\n\n"
                    "Para desactivar el inicio automático, desactiva el interruptor."
                ),
            },
            {
                "heading": "Solución de problemas",
                "body": (
                    "Dispositivo no detectado:\n"
                    "• Verifica que las reglas udev estén instaladas: "
                    "asm-cli udev write-rules --reload\n"
                    "• Reconecta el cable USB.\n"
                    "• Comprueba que el daemon esté activo: "
                    "systemctl --user status arctis-manager.service\n\n"
                    "Ajustes no aplicados:\n"
                    "• Reinicia el daemon: "
                    "systemctl --user restart arctis-manager.service\n\n"
                    "La interfaz no se abre:\n"
                    "• Ejecuta asm-gui -vvvv en una terminal para ver los registros detallados.\n"
                    "• Comprueba que el daemon esté activo (ver arriba).\n\n"
                    "El icono de bandeja no aparece al inicio:\n"
                    "• Activa \"Iniciar al arranque\" en Ajustes, o comprueba:\n"
                    "  systemctl --user status arctis-gui.service"
                ),
            },
        ],
    },
}

# ── Styles ─────────────────────────────────────────────────────────────────────

_LANG_BTN_INACTIVE = f"""
    QPushButton {{
        background-color: {BG_BUTTON};
        color: {TEXT_SECONDARY};
        border: 1px solid {BORDER};
        border-radius: 6px;
        padding: 4px 14px;
        font-size: 10pt;
        font-weight: bold;
    }}
    QPushButton:hover {{
        background-color: {BG_BUTTON_HOVER};
        color: {TEXT_PRIMARY};
    }}
"""

_LANG_BTN_ACTIVE = f"""
    QPushButton {{
        background-color: {ACCENT};
        color: #ffffff;
        border: 1px solid {ACCENT};
        border-radius: 6px;
        padding: 4px 14px;
        font-size: 10pt;
        font-weight: bold;
    }}
"""

_HEADING_STYLE = (
    f"color: {TEXT_PRIMARY}; font-size: 14pt; font-weight: bold; "
    "background: transparent; padding: 0;"
)

_BODY_STYLE = (
    f"color: {TEXT_SECONDARY}; font-size: 11pt; background: transparent; "
    "padding: 0; line-height: 1.5;"
)


# ── HelpPage ───────────────────────────────────────────────────────────────────

class HelpPage(QWidget):
    _current_lang: str = "en"

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setStyleSheet(f"background-color: {BG_MAIN};")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Header (title + lang selector) — fixed, outside scroll ──────────
        header_widget = QWidget()
        header_widget.setStyleSheet(f"background-color: {BG_MAIN};")
        header_layout = QVBoxLayout(header_widget)
        header_layout.setContentsMargins(36, 28, 36, 0)
        header_layout.setSpacing(6)

        title_row = QHBoxLayout()
        title_row.setSpacing(16)

        self._title_label = QLabel("Help")
        self._title_label.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 28pt; font-weight: bold; background: transparent;"
        )
        title_row.addWidget(self._title_label, stretch=1)

        # Report a bug button
        _BTN = (
            "QPushButton {{ background-color: {bg}; color: {fg}; border: none; "
            "border-radius: 6px; padding: 6px 14px; font-size: 9pt; }}"
            "QPushButton:hover {{ background-color: {hover}; }}"
        )
        report_btn = QPushButton("Report a bug")
        report_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        report_btn.setStyleSheet(_BTN.format(bg=BG_BUTTON, fg=TEXT_PRIMARY, hover=BG_BUTTON_HOVER))
        report_btn.clicked.connect(lambda: ReportBugDialog(parent=self).exec())
        title_row.addWidget(report_btn)

        # Language buttons
        lang_row = QHBoxLayout()
        lang_row.setSpacing(6)
        lang_row.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self._lang_label = QLabel("Language:")
        self._lang_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 10pt; background: transparent;"
        )
        lang_row.addWidget(self._lang_label)

        self._lang_buttons: dict[str, QPushButton] = {}
        for code, display in [("en", "EN"), ("fr", "FR"), ("es", "ES")]:
            btn = QPushButton(display)
            btn.setFixedHeight(30)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, c=code: self._set_language(c))
            lang_row.addWidget(btn)
            self._lang_buttons[code] = btn

        title_row.addLayout(lang_row)
        header_layout.addLayout(title_row)

        self._subtitle_label = QLabel("Arctis Sound Manager — User Manual")
        self._subtitle_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; font-size: 11pt; background: transparent;"
        )
        header_layout.addWidget(self._subtitle_label)
        header_layout.addSpacing(16)

        outer.addWidget(header_widget)

        divider = DividerLine()
        outer.addWidget(divider)

        # ── Scrollable content area ──────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(
            f"QScrollArea {{ background-color: {BG_MAIN}; border: none; }}"
        )

        self._content_widget = QWidget()
        self._content_widget.setStyleSheet(f"background-color: {BG_MAIN};")
        self._content_layout = QVBoxLayout(self._content_widget)
        self._content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._content_layout.setContentsMargins(36, 24, 36, 36)
        self._content_layout.setSpacing(0)

        scroll.setWidget(self._content_widget)
        outer.addWidget(scroll, stretch=1)

        self._set_language("en")

    # ── Language switching ─────────────────────────────────────────────────

    def _set_language(self, lang: str) -> None:
        self._current_lang = lang
        data = HELP_CONTENT[lang]

        # Update header labels
        self._title_label.setText(data["title"])
        self._subtitle_label.setText(data["subtitle"])
        self._lang_label.setText(data["lang_label"])

        # Update button styles
        for code, btn in self._lang_buttons.items():
            btn.setStyleSheet(_LANG_BTN_ACTIVE if code == lang else _LANG_BTN_INACTIVE)

        # Rebuild content
        self._clear_content()
        for section in data["sections"]:
            self._add_section(section["heading"], section["body"])

    def _clear_content(self) -> None:
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _add_section(self, heading: str, body: str) -> None:
        # Section card
        card = QWidget()
        card.setStyleSheet(
            f"background-color: {BG_CARD}; border-radius: 10px; border: 1px solid {BORDER};"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 16, 20, 16)
        card_layout.setSpacing(8)

        h_label = QLabel(heading)
        h_label.setStyleSheet(_HEADING_STYLE)
        card_layout.addWidget(h_label)

        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background-color: {BORDER};")
        card_layout.addWidget(sep)

        import re as _re
        html_body = _re.sub(
            r'(https?://[^\s]+)',
            r'<a href="\1" style="color: #FB4A00;">\1</a>',
            body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>"),
        )
        b_label = QLabel(html_body)
        b_label.setWordWrap(True)
        b_label.setStyleSheet(_BODY_STYLE)
        b_label.setTextFormat(Qt.TextFormat.RichText)
        b_label.setOpenExternalLinks(True)
        b_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        b_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        card_layout.addWidget(b_label)

        self._content_layout.addWidget(card)
        self._content_layout.addSpacing(12)
