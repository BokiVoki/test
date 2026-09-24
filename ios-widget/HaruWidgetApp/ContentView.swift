import SwiftUI
import WidgetKit

/// 이 미니앱의 화면은 이거 하나뿐 — 위젯에서 항목을 탭하면(체크 버튼이 아닌 곳)
/// 바로 이 빠른 추가 화면이 열림. 할일 관리·루틴 설정 등은 전부 하루 웹앱에서 함.
struct ContentView: View {
    @State private var title = ""
    @State private var saving = false
    @State private var message = ""

    var body: some View {
        NavigationStack {
            VStack(spacing: 16) {
                Text("이 앱은 하루 위젯 전용이에요.\n할일·루틴 관리는 하루 웹앱에서 하고,\n여기선 투두만 빠르게 추가할 수 있어요.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.top, 8)

                TextField("할일 추가", text: $title)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { addTodo() }

                Button(action: addTodo) {
                    Text(saving ? "추가 중…" : "추가")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .disabled(title.trimmingCharacters(in: .whitespaces).isEmpty || saving)

                if !message.isEmpty {
                    Text(message).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
            }
            .padding()
            .navigationTitle("하루 위젯")
        }
    }

    func addTodo() {
        let t = title.trimmingCharacters(in: .whitespaces)
        guard !t.isEmpty else { return }
        saving = true
        message = ""
        Task {
            let ok = await WidgetAPI.addTodo(title: t)
            saving = false
            if ok {
                title = ""
                message = "추가됐어요 ✓"
                WidgetCenter.shared.reloadAllTimelines()
            } else {
                message = "실패했어요, 다시 시도해주세요"
            }
        }
    }
}
