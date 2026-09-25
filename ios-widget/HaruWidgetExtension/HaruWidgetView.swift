import SwiftUI
import WidgetKit
import AppIntents

// 홈 화면(systemMedium) 전용 — 잠금화면은 HaruRoutineWidget/HaruTodoWidget(각각 accessoryRectangular)으로
// 분리됐음(2026-09-26, "잠금화면 위젯이 너무 작아서 루틴/투두를 양옆에 두 개로" 요청).
struct HaruWidgetView: View {
    var entry: HaruEntry
    let limit = 8

    private let slotOrder = ["morning", "lunch", "evening", ""]
    private let slotLabel: [String: String] = ["morning": "아침", "lunch": "점심", "evening": "저녁", "": "시간 무관"]

    var body: some View {
        mediumList()
            .containerBackground(for: .widget) { Color(.systemBackground) }
    }

    // 루틴을 슬롯(아침/점심/저녁/시간무관)별로 묶어서 보여주고, 남는 자리에 투두를 채움
    func mediumList() -> some View {
        var grouped: [String: [WidgetItem]] = [:]
        for r in entry.routines { grouped[r.slot ?? "", default: []].append(r) }

        var sections: [(label: String, items: [WidgetItem])] = []
        var shown = 0
        for slot in slotOrder {
            guard shown < limit, let items = grouped[slot], !items.isEmpty else { continue }
            let take = Array(items.prefix(limit - shown))
            sections.append((slotLabel[slot] ?? "", take))
            shown += take.count
        }
        let todoItems = Array(entry.todos.prefix(max(0, limit - shown)))

        return VStack(alignment: .leading, spacing: 5) {
            HStack {
                Text("하루").font(.headline)
                Spacer()
                Text("+ 추가").font(.caption).foregroundStyle(Color.accentColor)
            }
            if sections.isEmpty && todoItems.isEmpty {
                Text("오늘 체크할 루틴·투두가 없어요")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(sections, id: \.label) { section in
                    Text(section.label)
                        .font(.system(size: 9, weight: .semibold))
                        .foregroundStyle(.secondary)
                    ForEach(section.items, id: \.id) { item in
                        HaruItemRow(item: item, type: "routine")
                    }
                }
                if !todoItems.isEmpty {
                    if !sections.isEmpty {
                        Text("투두")
                            .font(.system(size: 9, weight: .semibold))
                            .foregroundStyle(.secondary)
                    }
                    ForEach(todoItems, id: \.id) { item in
                        HaruItemRow(item: item, type: "todo")
                    }
                }
            }
        }
        .padding(.vertical, 2)
        .widgetURL(URL(string: "haruwidget://add"))
    }
}

// 세 위젯(홈/루틴/투두)이 공용으로 쓰는 체크+제목 한 줄
struct HaruItemRow: View {
    var item: WidgetItem
    var type: String  // "routine" | "todo"
    var compact: Bool = false

    var body: some View {
        HStack(spacing: compact ? 4 : 6) {
            Button(intent: CheckItemIntent(itemType: type, itemId: item.id)) {
                Image(systemName: "circle")
                    .font(.system(size: compact ? 10 : 13))
            }
            .buttonStyle(.plain)
            Text(item.title)
                .font(.system(size: compact ? 11 : 13))
                .lineLimit(1)
            if !compact { Spacer() }
        }
    }
}
