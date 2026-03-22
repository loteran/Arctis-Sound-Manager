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
                "heading": "Equalizer",
                "body": (
                    "The Equalizer page lets you adjust the frequency response of your headset.\n\n"
                    "• Each slider controls one frequency band (31 Hz → 16 kHz).\n"
                    "• Drag a slider up to boost that frequency, down to cut it.\n"
                    "• Changes are sent to the device immediately.\n"
                    "• The page polls the device every 500 ms when visible, so hardware changes "
                    "(e.g. adjusting bands from the DAC) are reflected automatically.\n\n"
                    "Tip: a flat EQ (all sliders at 0 dB) is the neutral starting point."
                ),
            },
            {
                "heading": "Headset / DAC Infos",
                "body": (
                    "This page displays technical information about the connected device:\n\n"
                    "• Device name and model\n"
                    "• USB Vendor ID and Product ID\n"
                    "• Active noise cancelling (ANC) state: Off / Transparent / ANC\n\n"
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
                    "• Wireless transmitter power\n\n"
                    "Each setting is applied to the device as soon as you change it."
                ),
            },
            {
                "heading": "System tray",
                "body": (
                    "The tray icon provides quick access without opening the full window.\n\n"
                    "• Left-click or right-click the icon to open the context menu.\n"
                    "• The menu shows the current device status (battery, connection, etc.).\n"
                    "• Open App — brings the main window to the foreground.\n"
                    "• EQ toggle — switches the equalizer preset between Custom and Sonar.\n"
                    "• Exit — fully quits the application (tray and daemon).\n\n"
                    "If you close the main window, the tray stays active. The app only stops "
                    "when you choose Exit from the tray menu."
                ),
            },
            {
                "heading": "Surround sound",
                "body": (
                    "Arctis Sound Manager supports virtual surround sound through PipeWire and "
                    "HeSuVi-compatible convolution filters.\n\n"
                    "── How it works ──\n"
                    "A virtual 7.1 surround sink is created in PipeWire. Audio routed to it is "
                    "processed by HRTF convolution filters (HeSuVi) and then folded down to "
                    "stereo for your headset. The result is a convincing spatial audio "
                    "experience on any stereo headphone.\n\n"
                    "── Setup ──\n"
                    "1. Install HeSuVi (https://sourceforge.net/projects/hesuvi/) or use "
                    "an equivalent set of stereo HRTF .wav files.\n"
                    "2. Configure a PipeWire convolution filter module pointing to the HeSuVi "
                    "impulse responses. Place the config in:\n"
                    "   ~/.config/pipewire/pipewire.conf.d/hesuvi.conf\n"
                    "3. Reload PipeWire:  systemctl --user restart pipewire\n"
                    "4. A virtual sink named 'hesuvi' (or similar) will appear. Route your "
                    "game audio to it from Arctis Sound Manager by moving the Game stream to HDMI "
                    "or by setting the hesuvi sink as the default in your system settings.\n\n"
                    "── EQ toggle (tray) ──\n"
                    "The tray menu has an EQ toggle button that switches between the Custom "
                    "preset (your own EQ curve) and the Sonar preset. This toggle writes to\n"
                    "   ~/.config/arctis_manager/.eq_mode\n"
                    "and runs the toggle_sonar.py script you can customize in that folder.\n\n"
                    "── Recommended tool ──\n"
                    "IrateGoose is a GUI that simplifies the entire PipeWire surround setup:\n"
                    "   https://github.com/Barafu/IrateGoose"
                ),
            },
            {
                "heading": "Autostart at login",
                "body": (
                    "To start Arctis Sound Manager automatically when you log in:\n\n"
                    "1. Copy the Arctis Sound Manager Systray desktop entry to your autostart folder:\n"
                    "   cp ~/.local/share/applications/ArctisManager.desktop "
                    "~/.config/autostart/\n"
                    "2. Edit the copied file and add --systray to the Exec line so the window "
                    "does not open at login:\n"
                    "   Exec=asm-gui --systray\n\n"
                    "The tray icon will appear after login without the main window opening."
                ),
            },
            {
                "heading": "Troubleshooting",
                "body": (
                    "Device not detected:\n"
                    "• Make sure udev rules are installed: run asm-cli udev write-rules --reload\n"
                    "• Reconnect the USB cable.\n"
                    "• Check that asm-daemon is running: systemctl --user status asm-daemon\n\n"
                    "Settings not applied:\n"
                    "• Restart the daemon: systemctl --user restart asm-daemon\n\n"
                    "GUI does not open:\n"
                    "• Run asm-gui -vvvv in a terminal to see detailed logs.\n"
                    "• Check that asm-daemon is running (see above)."
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
                "heading": "Égaliseur",
                "body": (
                    "La page Égaliseur permet d'ajuster la réponse en fréquence du casque.\n\n"
                    "• Chaque slider contrôle une bande de fréquence (31 Hz → 16 kHz).\n"
                    "• Montez un slider pour amplifier cette fréquence, descendez pour la couper.\n"
                    "• Les modifications sont envoyées à l'appareil immédiatement.\n"
                    "• La page interroge l'appareil toutes les 500 ms quand elle est visible, "
                    "donc les modifications faites depuis le DAC sont répercutées automatiquement.\n\n"
                    "Conseil : un égaliseur à plat (tous les sliders à 0 dB) est le point "
                    "de départ neutre."
                ),
            },
            {
                "heading": "Casque / DAC — Infos",
                "body": (
                    "Cette page affiche des informations techniques sur l'appareil connecté :\n\n"
                    "• Nom et modèle de l'appareil\n"
                    "• Identifiant USB (Vendor ID et Product ID)\n"
                    "• État de la réduction de bruit active (ANC) : Désactivé / Transparent / ANC\n\n"
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
                    "• Puissance du transmetteur sans fil\n\n"
                    "Chaque paramètre est appliqué à l'appareil dès sa modification."
                ),
            },
            {
                "heading": "Barre système (tray)",
                "body": (
                    "L'icône de la barre système permet un accès rapide sans ouvrir la fenêtre.\n\n"
                    "• Clic gauche ou droit sur l'icône pour ouvrir le menu contextuel.\n"
                    "• Le menu affiche le statut actuel (batterie, connexion, etc.).\n"
                    "• Ouvrir — amène la fenêtre principale au premier plan.\n"
                    "• Bascule EQ — change le préréglage égaliseur entre Personnalisé et Sonar.\n"
                    "• Quitter — ferme complètement l'application.\n\n"
                    "Fermer la fenêtre principale laisse le tray actif. L'application ne s'arrête "
                    "que si vous choisissez Quitter depuis le menu du tray."
                ),
            },
            {
                "heading": "Son surround",
                "body": (
                    "Arctis Sound Manager prend en charge le son surround virtuel via PipeWire et des "
                    "filtres de convolution compatibles HeSuVi.\n\n"
                    "── Fonctionnement ──\n"
                    "Un sink 7.1 virtuel est créé dans PipeWire. Le son qui y est acheminé est "
                    "traité par des filtres HRTF (HeSuVi) puis réduit en stéréo pour le casque. "
                    "Le résultat est une spatialisation audio convaincante sur tout casque stéréo.\n\n"
                    "── Mise en place ──\n"
                    "1. Installez HeSuVi (https://sourceforge.net/projects/hesuvi/) ou utilisez "
                    "un jeu de fichiers HRTF stéréo .wav équivalent.\n"
                    "2. Configurez un module de convolution PipeWire pointant vers les réponses "
                    "impulsionnelles HeSuVi. Placez la config dans :\n"
                    "   ~/.config/pipewire/pipewire.conf.d/hesuvi.conf\n"
                    "3. Rechargez PipeWire :  systemctl --user restart pipewire\n"
                    "4. Un sink virtuel nommé 'hesuvi' (ou similaire) apparaît. Routez votre "
                    "audio jeu vers lui depuis Arctis Sound Manager via les boutons H ou en le "
                    "définissant comme sortie par défaut dans les paramètres système.\n\n"
                    "── Bascule EQ (tray) ──\n"
                    "Le menu du tray contient un bouton qui bascule entre le préréglage "
                    "Personnalisé (votre courbe EQ) et le préréglage Sonar. Ce bouton écrit dans\n"
                    "   ~/.config/arctis_manager/.eq_mode\n"
                    "et exécute le script toggle_sonar.py que vous pouvez personnaliser.\n\n"
                    "── Outil recommandé ──\n"
                    "IrateGoose est une interface graphique qui simplifie toute la configuration "
                    "du surround PipeWire :\n"
                    "   https://github.com/Barafu/IrateGoose"
                ),
            },
            {
                "heading": "Démarrage automatique",
                "body": (
                    "Pour démarrer Arctis Sound Manager automatiquement à la connexion :\n\n"
                    "1. Copiez l'entrée bureau dans le dossier autostart :\n"
                    "   cp ~/.local/share/applications/ArctisManager.desktop "
                    "~/.config/autostart/\n"
                    "2. Éditez le fichier copié et ajoutez --systray sur la ligne Exec afin que "
                    "la fenêtre ne s'ouvre pas au démarrage :\n"
                    "   Exec=asm-gui --systray\n\n"
                    "L'icône de tray apparaîtra après connexion sans que la fenêtre s'ouvre."
                ),
            },
            {
                "heading": "Dépannage",
                "body": (
                    "Appareil non détecté :\n"
                    "• Vérifiez que les règles udev sont installées : "
                    "asm-cli udev write-rules --reload\n"
                    "• Rebranchez le câble USB.\n"
                    "• Vérifiez que asm-daemon fonctionne : "
                    "systemctl --user status asm-daemon\n\n"
                    "Paramètres non appliqués :\n"
                    "• Redémarrez le daemon : systemctl --user restart asm-daemon\n\n"
                    "Interface qui ne s'ouvre pas :\n"
                    "• Lancez asm-gui -vvvv dans un terminal pour voir les logs détaillés.\n"
                    "• Vérifiez que asm-daemon fonctionne (voir ci-dessus)."
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
                "heading": "Ecualizador",
                "body": (
                    "La página del Ecualizador permite ajustar la respuesta en frecuencia de los "
                    "auriculares.\n\n"
                    "• Cada deslizador controla una banda de frecuencia (31 Hz → 16 kHz).\n"
                    "• Sube el deslizador para realzar esa frecuencia, bájalo para atenuarla.\n"
                    "• Los cambios se envían al dispositivo de forma inmediata.\n"
                    "• La página consulta el dispositivo cada 500 ms cuando es visible, por lo "
                    "que los cambios realizados desde el DAC se reflejan automáticamente.\n\n"
                    "Consejo: un EQ plano (todos los deslizadores a 0 dB) es el punto de "
                    "partida neutro."
                ),
            },
            {
                "heading": "Auriculares / DAC — Información",
                "body": (
                    "Esta página muestra información técnica sobre el dispositivo conectado:\n\n"
                    "• Nombre y modelo del dispositivo\n"
                    "• Identificador USB (Vendor ID y Product ID)\n"
                    "• Estado de cancelación activa de ruido (ANC): Desactivado / Transparente / "
                    "ANC\n\n"
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
                    "• Potencia del transmisor inalámbrico\n\n"
                    "Cada ajuste se aplica al dispositivo en cuanto lo modificas."
                ),
            },
            {
                "heading": "Bandeja del sistema",
                "body": (
                    "El icono de bandeja proporciona acceso rápido sin abrir la ventana "
                    "completa.\n\n"
                    "• Clic izquierdo o derecho en el icono para abrir el menú contextual.\n"
                    "• El menú muestra el estado actual del dispositivo (batería, conexión, etc.).\n"
                    "• Abrir — lleva la ventana principal al primer plano.\n"
                    "• Cambiar EQ — alterna el preset del ecualizador entre Personalizado y Sonar.\n"
                    "• Salir — cierra completamente la aplicación.\n\n"
                    "Cerrar la ventana principal deja activa la bandeja. La aplicación solo se "
                    "detiene si eliges Salir desde el menú de la bandeja."
                ),
            },
            {
                "heading": "Sonido envolvente",
                "body": (
                    "Arctis Sound Manager admite sonido envolvente virtual mediante PipeWire y filtros "
                    "de convolución compatibles con HeSuVi.\n\n"
                    "── Cómo funciona ──\n"
                    "Se crea un sink 7.1 virtual en PipeWire. El audio enrutado a él es procesado "
                    "por filtros HRTF (HeSuVi) y luego reducido a estéreo para los auriculares. "
                    "El resultado es una experiencia de audio espacial convincente en cualquier "
                    "auricular estéreo.\n\n"
                    "── Configuración ──\n"
                    "1. Instala HeSuVi (https://sourceforge.net/projects/hesuvi/) o usa un "
                    "conjunto de archivos HRTF estéreo .wav equivalente.\n"
                    "2. Configura un módulo de convolución en PipeWire apuntando a las respuestas "
                    "al impulso de HeSuVi. Coloca la config en:\n"
                    "   ~/.config/pipewire/pipewire.conf.d/hesuvi.conf\n"
                    "3. Recarga PipeWire:  systemctl --user restart pipewire\n"
                    "4. Aparecerá un sink virtual llamado 'hesuvi' (o similar). Enruta el audio "
                    "de juegos hacia él desde Arctis Sound Manager usando el botón H o estableciéndolo "
                    "como salida predeterminada en los ajustes del sistema.\n\n"
                    "── Cambio de EQ (bandeja) ──\n"
                    "El menú de la bandeja incluye un botón que alterna entre el perfil "
                    "Personalizado (tu curva EQ) y el perfil Sonar. Este botón escribe en\n"
                    "   ~/.config/arctis_manager/.eq_mode\n"
                    "y ejecuta el script toggle_sonar.py que puedes personalizar.\n\n"
                    "── Herramienta recomendada ──\n"
                    "IrateGoose es una interfaz gráfica que simplifica toda la configuración "
                    "del surround en PipeWire:\n"
                    "   https://github.com/Barafu/IrateGoose"
                ),
            },
            {
                "heading": "Inicio automático",
                "body": (
                    "Para iniciar Arctis Sound Manager automáticamente al iniciar sesión:\n\n"
                    "1. Copia la entrada de escritorio a la carpeta de autoarranque:\n"
                    "   cp ~/.local/share/applications/ArctisManager.desktop "
                    "~/.config/autostart/\n"
                    "2. Edita el archivo copiado y añade --systray en la línea Exec para que la "
                    "ventana no se abra al iniciar sesión:\n"
                    "   Exec=asm-gui --systray\n\n"
                    "El icono de bandeja aparecerá después del inicio de sesión sin abrir la "
                    "ventana principal."
                ),
            },
            {
                "heading": "Solución de problemas",
                "body": (
                    "Dispositivo no detectado:\n"
                    "• Verifica que las reglas udev estén instaladas: "
                    "asm-cli udev write-rules --reload\n"
                    "• Reconecta el cable USB.\n"
                    "• Comprueba que asm-daemon esté activo: "
                    "systemctl --user status asm-daemon\n\n"
                    "Ajustes no aplicados:\n"
                    "• Reinicia el daemon: systemctl --user restart asm-daemon\n\n"
                    "La interfaz no se abre:\n"
                    "• Ejecuta asm-gui -vvvv en una terminal para ver los registros detallados.\n"
                    "• Comprueba que asm-daemon esté activo (ver arriba)."
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
