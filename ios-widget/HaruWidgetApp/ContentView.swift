import SwiftUI
import WidgetKit

/// 이 미니앱의 화면은 이거 하나뿐 — 위젯에서 항목을 탭하면(체크 버튼이 아닌 곳)
/// 바로 이 빠른 추가 화면이 열림. 할일·루틴 관리는 전부 하루 웹앱에서 함.
/// 상단 세그먼트로 "주머니"(할일 빠른입력) / "가계부"(금액·카테고리만 빠른입력, 2026-09-28)를 전환.
enum QuickMode: String, CaseIterable {
    case pocket = "주머니"
    case ledger = "가계부"
}

struct ContentView: View {
    @State private var mode: QuickMode = .pocket

    // 주머니
    @State private var title = ""
    @State private var savingTodo = false
    @State private var todoMessage = ""

    // 가계부 — 금액·카테고리만(빨리 입력하는 용도라 날짜는 항상 오늘, 메모는 없음)
    @State private var ledgerType = "expense" // "expense" | "income"
    @State private var amountText = ""
    @State private var category = ""
    @State private var savingLedger = false
    @State private var ledgerMessage = ""

    private let expenseCats = ["식비", "카페/간식", "교통", "생활용품", "쇼핑", "의료", "문화/여가", "주거/공과금", "기타"]
    private let incomeCats = ["급여", "부수입", "용돈", "기타"]

    var body: some View {
        NavigationStack {
            VStack(spacing: 16) {
                Picker("", selection: $mode) {
                    ForEach(QuickMode.allCases, id: \.self) { m in
                        Text(m.rawValue).tag(m)
                    }
                }
                .pickerStyle(.segmented)
                .padding(.top, 4)

                if mode == .pocket {
                    pocketForm
                } else {
                    ledgerForm
                }

                Spacer()
            }
            .padding()
            .navigationTitle("하루 위젯")
        }
    }

    private var pocketForm: some View {
        VStack(spacing: 16) {
            Text("할일·루틴 관리는 하루 웹앱에서 하고,\n여기선 주머니(인박스)에 빠르게 담을 수 있어요.")
                .font(.footnote)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)

            TextField("주머니에 추가", text: $title)
                .textFieldStyle(.roundedBorder)
                .onSubmit { addTodo() }

            Button(action: addTodo) {
                Text(savingTodo ? "추가 중…" : "추가")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .disabled(title.trimmingCharacters(in: .whitespaces).isEmpty || savingTodo)

            if !todoMessage.isEmpty {
                Text(todoMessage).font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private var ledgerForm: some View {
        VStack(spacing: 16) {
            Text("가계부에 금액·카테고리만 빠르게 기록해요.\n날짜는 오늘로 들어가고, 메모는 나중에 웹앱에서 채울 수 있어요.")
                .font(.footnote)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)

            Picker("", selection: $ledgerType) {
                Text("지출").tag("expense")
                Text("수입").tag("income")
            }
            .pickerStyle(.segmented)
            .onChange(of: ledgerType) { _ in category = "" }

            TextField("금액", text: $amountText)
                .textFieldStyle(.roundedBorder)
                .keyboardType(.numberPad)

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(ledgerType == "income" ? incomeCats : expenseCats, id: \.self) { c in
                        Button(c) { category = c }
                            .buttonStyle(.bordered)
                            .tint(category == c ? .accentColor : .gray)
                    }
                }
                .padding(.vertical, 2)
            }

            TextField("카테고리(위에서 고르거나 직접 입력)", text: $category)
                .textFieldStyle(.roundedBorder)
                .onSubmit { addLedger() }

            Button(action: addLedger) {
                Text(savingLedger ? "추가 중…" : "가계부에 추가")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .disabled(amountText.trimmingCharacters(in: .whitespaces).isEmpty
                      || category.trimmingCharacters(in: .whitespaces).isEmpty
                      || savingLedger)

            if !ledgerMessage.isEmpty {
                Text(ledgerMessage).font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    func addTodo() {
        let t = title.trimmingCharacters(in: .whitespaces)
        guard !t.isEmpty else { return }
        savingTodo = true
        todoMessage = ""
        Task {
            let ok = await WidgetAPI.addTodo(title: t)
            savingTodo = false
            if ok {
                title = ""
                todoMessage = "추가됐어요 ✓"
                WidgetCenter.shared.reloadAllTimelines()
            } else {
                todoMessage = "실패했어요, 다시 시도해주세요"
            }
        }
    }

    func addLedger() {
        let amt = Int(amountText.trimmingCharacters(in: .whitespaces)) ?? 0
        let cat = category.trimmingCharacters(in: .whitespaces)
        guard amt > 0, !cat.isEmpty else { return }
        savingLedger = true
        ledgerMessage = ""
        Task {
            let ok = await WidgetAPI.addLedger(amount: amt, category: cat, ledgerType: ledgerType)
            savingLedger = false
            if ok {
                amountText = ""
                category = ""
                ledgerMessage = "가계부에 추가됐어요 ✓"
            } else {
                ledgerMessage = "실패했어요, 다시 시도해주세요"
            }
        }
    }
}
