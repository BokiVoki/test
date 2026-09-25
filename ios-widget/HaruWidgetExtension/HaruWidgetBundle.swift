import SwiftUI
import WidgetKit

@main
struct HaruWidgetBundle: WidgetBundle {
    var body: some Widget {
        HaruWidget()         // 홈 화면 — 루틴+투두, 루틴은 아침/점심/저녁별로 묶어서
        HaruRoutineWidget()  // 잠금화면 — 루틴만
        HaruTodoWidget()     // 잠금화면 — 투두만 (루틴 위젯이랑 양옆에 나란히)
    }
}
