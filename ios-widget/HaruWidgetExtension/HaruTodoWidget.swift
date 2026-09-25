import SwiftUI
import WidgetKit

// 잠금화면 전용(accessoryRectangular) — 투두만. HaruRoutineWidget과 나란히(양옆) 놓을 수 있게
// 홈 화면 위젯(HaruWidget)에서 분리했음(2026-09-26).
struct HaruTodoWidgetView: View {
    var entry: HaruEntry

    var body: some View {
        let rows = entry.todos.prefix(4)
        VStack(alignment: .leading, spacing: 3) {
            if rows.isEmpty {
                Text("오늘 투두 없음")
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
            } else {
                ForEach(rows, id: \.id) { item in
                    HaruItemRow(item: item, type: "todo", compact: true)
                }
            }
        }
        .widgetURL(URL(string: "haruwidget://add"))
        .containerBackground(for: .widget) { Color.clear }
    }
}

struct HaruTodoWidget: Widget {
    let kind: String = "HaruTodoWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: HaruProvider()) { entry in
            HaruTodoWidgetView(entry: entry)
        }
        .configurationDisplayName("하루 투두")
        .description("오늘 할 투두 (잠금화면용)")
        .supportedFamilies([.accessoryRectangular])
    }
}
