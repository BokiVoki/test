import SwiftUI
import WidgetKit
import AppIntents

struct HaruWidgetView: View {
    @Environment(\.widgetFamily) var family
    var entry: HaruEntry

    /// 루틴을 먼저, 그다음 투두 — 루틴("쫙쫙 체크"하고 싶다던 습관형 항목)을 우선 노출
    var combined: [(item: WidgetItem, type: String)] {
        entry.routines.map { ($0, "routine") } + entry.todos.map { ($0, "todo") }
    }

    var body: some View {
        switch family {
        case .accessoryRectangular:
            compactList(limit: 4)
                .containerBackground(for: .widget) { Color.clear }
        default:
            mediumList(limit: 8)
                .containerBackground(for: .widget) { Color(.systemBackground) }
        }
    }

    // 잠금화면(accessoryRectangular, ~가로 160pt) — 아주 짧게 압축해서 몇 개만
    func compactList(limit: Int) -> some View {
        let rows = combined.prefix(limit)
        return VStack(alignment: .leading, spacing: 3) {
            if rows.isEmpty {
                Text("오늘 할 일 없음")
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
            } else {
                ForEach(rows, id: \.item.id) { row in
                    HStack(spacing: 4) {
                        Button(intent: CheckItemIntent(itemType: row.type, itemId: row.item.id)) {
                            Image(systemName: "circle")
                                .font(.system(size: 10))
                        }
                        .buttonStyle(.plain)
                        Text(row.item.title)
                            .font(.system(size: 11))
                            .lineLimit(1)
                    }
                }
            }
        }
        .widgetURL(URL(string: "haruwidget://add"))
    }

    // 홈 화면(systemMedium) — 여유 있게 체크리스트
    func mediumList(limit: Int) -> some View {
        let rows = combined.prefix(limit)
        return VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("하루").font(.headline)
                Spacer()
                Text("+ 추가").font(.caption).foregroundStyle(Color.accentColor)
            }
            if rows.isEmpty {
                Text("오늘 체크할 루틴·투두가 없어요")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(rows, id: \.item.id) { row in
                    HStack(spacing: 6) {
                        Button(intent: CheckItemIntent(itemType: row.type, itemId: row.item.id)) {
                            Image(systemName: "circle")
                        }
                        .buttonStyle(.plain)
                        Text(row.item.title)
                            .font(.system(size: 13))
                            .lineLimit(1)
                        Spacer()
                    }
                }
            }
        }
        .padding(.vertical, 2)
        .widgetURL(URL(string: "haruwidget://add"))
    }
}
