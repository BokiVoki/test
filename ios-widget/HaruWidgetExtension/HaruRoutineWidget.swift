import SwiftUI
import WidgetKit

// 잠금화면 전용(accessoryRectangular) — 루틴만. HaruTodoWidget과 나란히(양옆) 놓을 수 있게
// 홈 화면 위젯(HaruWidget)에서 분리했음(2026-09-26).
struct HaruRoutineWidgetView: View {
    var entry: HaruEntry

    var body: some View {
        let rows = entry.routines.prefix(4)
        VStack(alignment: .leading, spacing: 3) {
            if rows.isEmpty {
                Text("오늘 루틴 없음")
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
            } else {
                ForEach(rows, id: \.id) { item in
                    HaruItemRow(item: item, type: "routine", compact: true)
                }
            }
        }
        .widgetURL(URL(string: "haruwidget://add"))
        .containerBackground(for: .widget) { Color.clear }
    }
}

struct HaruRoutineWidget: Widget {
    let kind: String = "HaruRoutineWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: HaruProvider()) { entry in
            HaruRoutineWidgetView(entry: entry)
        }
        .configurationDisplayName("하루 루틴")
        .description("오늘 체크할 루틴 (잠금화면용)")
        .supportedFamilies([.accessoryRectangular])
    }
}
