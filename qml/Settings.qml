import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import RinUI
import ClassWidgets.Plugins

PluginPage {
    id: root
    pluginId: "com.hpdnya.ntp"
    title: "NTP 校时"
    property var state: ({})
    function refresh() { if (backend) state = backend.snapshot() }
    Component.onCompleted: refresh()
    Connections {
        target: backend
        function onChanged() { root.refresh() }
    }

    SettingCard {
        Layout.fillWidth: true
        title: "接管课表偏移"
        description: "启用后锁定 schedule.time_offset；关闭时有条件恢复原值。"
        Switch {
            checked: root.state.active || false
            onClicked: backend.enable(checked)
        }
    }
    Repeater {
        model: [
            {key: "servers", label: "服务器（英文逗号分隔）"},
            {key: "interval_minutes", label: "同步间隔（分钟）"},
            {key: "compensation", label: "校铃补偿（秒，可为负）"},
            {key: "timeout", label: "单请求超时（秒）"},
            {key: "max_delay", label: "最大往返延迟（秒）"},
            {key: "max_offset", label: "最大绝对 NTP 时差（秒）"}
        ]
        delegate: SettingCard {
            required property var modelData
            Layout.fillWidth: true
            title: modelData.label
            TextField {
                width: 300
                text: modelData.key === "servers" ? (root.state.servers || []).join(",") : String(root.state[modelData.key] ?? "")
                onEditingFinished: backend.updateSetting(modelData.key, text)
            }
        }
    }
    Button { text: "立即同步"; enabled: root.state.active || false; onClicked: backend.sync() }
    Text {
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
        text: (root.state.status || "") + "\n最近成功：" + (root.state.success || "—")
            + "\n来源：" + (root.state.source || "—")
            + "\nNTP 时差：" + Number(root.state.offset_ms || 0).toFixed(2) + " ms"
            + "；RTT：" + Number(root.state.delay_ms || 0).toFixed(2) + " ms"
            + "\n最终课表偏移：" + (root.state.final || 0) + " 秒"
    }
}