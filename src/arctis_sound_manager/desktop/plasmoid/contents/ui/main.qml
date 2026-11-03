/*
    SPDX-FileCopyrightText: 2026 loteran
    SPDX-License-Identifier: GPL-3.0-or-later

    Headset status from the ASM daemon. Reads GetStatus / GetSettings once
    the daemon's bus name appears, then follows StatusChanged, which carries
    the whole GetStatus payload. The slow timer only matters for daemons
    older than that signal.
*/
import QtQuick
import QtQuick.Layouts

import org.kde.kirigami as Kirigami
import org.kde.plasma.components as PlasmaComponents
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasmoid
import org.kde.plasma.plasma5support as P5Support
import org.kde.plasma.workspace.dbus as DBus

import "tr.js" as T

PlasmoidItem {
    id: root

    readonly property string busName: "name.giacomofurlan.ArctisManager.Next"
    readonly property string basePath: "/name/giacomofurlan/ArctisManager/Next"

    readonly property bool daemonUp: serviceWatcher.registered
    // GetStatus flattened to {variable: value}: the category a variable
    // sits in differs between headset families.
    property var status: ({})
    property string deviceName: ""

    readonly property var power: status.headset_power_status
    // Same vocabulary as power_status.py: 'standby' and the like are
    // neither on nor off.
    readonly property bool headsetOn: ["on", "online", "cable_charging"].indexOf(power) >= 0
    readonly property bool headsetOff: ["off", "offline"].indexOf(power) >= 0
    readonly property var headsetBattery: status.headset_battery_charge
    // Base station charging slot (Nova Pro Wireless family).
    readonly property var spareBattery: status.charge_slot_battery_charge
    // Arctis Pro Wireless: whether a spare is in the base, no level (#367).
    readonly property var spareSlot: status.charge_slot_battery
    readonly property bool hasBattery: headsetBattery !== undefined && !headsetOff

    function callDaemon(object, member, onValue) {
        DBus.SessionBus.asyncCall({
            service: busName,
            path: basePath + "/" + object,
            iface: busName + "." + object,
            member: member,
        }, reply => onValue(JSON.parse(String(reply.value))), () => {})
    }

    function applyStatus(payload) {
        const flat = {};
        for (const category in payload) {
            for (const variable in payload[category]) {
                flat[variable] = payload[category][variable].value;
            }
        }
        status = flat;
    }

    function refresh() {
        if (!daemonUp) {
            status = {};
            deviceName = "";
            return;
        }
        callDaemon("Status", "GetStatus", applyStatus);
        callDaemon("Settings", "GetSettings", settings => {
            deviceName = settings.device_name || "";
        });
    }

    function tr(key, args) {
        return T.tr(key, args);
    }

    function field(key) {
        return tr("field_label", {label: tr(key)});
    }

    function powerText(value) {
        if (["on", "online"].indexOf(value) >= 0)
            return tr("powered_on");
        if (["off", "offline"].indexOf(value) >= 0)
            return tr("powered_off");
        return labelText(value);
    }

    function batteryIcon(percent) {
        const level = Math.max(0, Math.min(100, Math.round(percent / 10) * 10));
        return "battery-" + String(level).padStart(3, "0");
    }

    function labelText(value) {
        switch (value) {
        case "on": return tr("on");
        case "off": return tr("off");
        case "cable_charging": return tr("charging");
        case "muted": return tr("muted");
        case "unmuted": return tr("unmuted");
        case "transparent":
        case "transparency": return tr("transparency");
        }
        return value === undefined ? "" : String(value);
    }

    function openAsm() {
        launcher.connectSource("asm-gui");
    }

    Plasmoid.icon: "audio-headset"
    switchWidth: Kirigami.Units.gridUnit * 12
    switchHeight: Kirigami.Units.gridUnit * 8
    Plasmoid.status: daemonUp ? PlasmaCore.Types.ActiveStatus : PlasmaCore.Types.PassiveStatus

    toolTipMainText: deviceName || "Arctis Sound Manager"
    toolTipSubText: {
        if (!daemonUp)
            return tr("daemon_not_running");
        if (headsetOff)
            return tr("headset_off");
        return hasBattery ? tr("battery_percent", {percent: headsetBattery}) : powerText(power);
    }

    DBus.DBusServiceWatcher {
        id: serviceWatcher
        busType: DBus.BusType.Session
        watchedService: root.busName
        onRegisteredChanged: root.refresh()
    }

    DBus.SignalWatcher {
        busType: DBus.BusType.Session
        service: root.busName
        path: root.basePath + "/Status"
        iface: root.busName + ".Status"
        enabled: root.daemonUp

        function dbusStatusChanged(payload) {
            root.applyStatus(JSON.parse(String(payload)));
        }
    }

    Timer {
        interval: 30000
        repeat: true
        running: root.daemonUp
        onTriggered: root.refresh()
    }

    P5Support.DataSource {
        id: launcher
        engine: "executable"
        onNewData: sourceName => disconnectSource(sourceName)
    }

    Component.onCompleted: refresh()

    Plasmoid.contextualActions: [
        PlasmaCore.Action {
            text: root.tr("open_app")
            icon.name: "audio-headset"
            onTriggered: root.openAsm()
        }
    ]

    compactRepresentation: MouseArea {
        Layout.minimumWidth: row.implicitWidth
        acceptedButtons: Qt.LeftButton | Qt.MiddleButton
        onClicked: mouse => {
            if (mouse.button === Qt.MiddleButton)
                root.openAsm();
            else
                root.expanded = !root.expanded;
        }

        RowLayout {
            id: row
            anchors.fill: parent
            spacing: Kirigami.Units.smallSpacing

            Kirigami.Icon {
                Layout.fillHeight: true
                Layout.preferredWidth: height
                source: "audio-headset"
                opacity: root.headsetOff || !root.daemonUp ? 0.5 : 1
            }
            PlasmaComponents.Label {
                visible: root.hasBattery
                text: root.headsetBattery + "%"
            }
        }
    }

    fullRepresentation: ColumnLayout {
        Layout.minimumWidth: Kirigami.Units.gridUnit * 14
        Layout.preferredHeight: implicitHeight
        spacing: Kirigami.Units.largeSpacing

        Kirigami.Heading {
            Layout.fillWidth: true
            level: 3
            text: root.deviceName || "Arctis Sound Manager"
            elide: Text.ElideRight
        }

        PlasmaComponents.Label {
            Layout.fillWidth: true
            visible: !root.daemonUp
            text: root.tr("daemon_not_running")
            wrapMode: Text.Wrap
        }

        Kirigami.FormLayout {
            Layout.fillWidth: true
            visible: root.daemonUp

            PlasmaComponents.Label {
                Kirigami.FormData.label: root.field("headset")
                visible: root.power !== undefined
                text: root.powerText(root.power)
            }
            RowLayout {
                Kirigami.FormData.label: root.field("battery")
                visible: root.hasBattery
                Kirigami.Icon {
                    implicitWidth: Kirigami.Units.iconSizes.small
                    implicitHeight: implicitWidth
                    source: root.batteryIcon(root.headsetBattery || 0)
                }
                PlasmaComponents.Label {
                    text: root.headsetBattery + "%"
                }
            }
            RowLayout {
                Kirigami.FormData.label: root.field("spare_battery")
                visible: root.spareBattery !== undefined
                Kirigami.Icon {
                    implicitWidth: Kirigami.Units.iconSizes.small
                    implicitHeight: implicitWidth
                    source: root.batteryIcon(root.spareBattery || 0)
                }
                PlasmaComponents.Label {
                    text: root.spareBattery + "%"
                }
            }
            PlasmaComponents.Label {
                Kirigami.FormData.label: root.field("spare_battery")
                visible: root.spareBattery === undefined && root.spareSlot !== undefined
                text: root.spareSlot === "no_battery" ? root.tr("no_battery") : root.tr("battery_in_base")
            }
            PlasmaComponents.Label {
                Kirigami.FormData.label: root.field("noise_cancelling")
                visible: root.status.noise_cancelling !== undefined && !root.headsetOff
                text: root.labelText(root.status.noise_cancelling)
            }
            PlasmaComponents.Label {
                Kirigami.FormData.label: root.field("microphone")
                visible: root.status.mic_status !== undefined && !root.headsetOff
                text: root.labelText(root.status.mic_status)
            }
        }

        PlasmaComponents.Button {
            Layout.alignment: Qt.AlignRight
            icon.name: "configure"
            text: root.tr("open_app")
            onClicked: root.openAsm()
        }
    }
}
