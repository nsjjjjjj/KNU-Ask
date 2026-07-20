const notices = {
  academic: [
    ["필독", "2026-1학기 강의평가 결과 열람 안내(교원용)", "07.20"],
    ["학사", "2026-2학기 예비수강신청 이관 결과 조회 안내", "07.16"],
    ["학사", "2026-2학기 수강신청 안내 및 강의시간표 배부", "07.07"],
    ["학사", "2026-2학기 복학 신청 안내", "06.22"]
  ],
  scholarship: [
    ["장학", "2026학년도 2학기 교내장학금 신청 안내", "07.18"],
    ["장학", "국가근로장학생 추가 선발 안내", "07.15"],
    ["장학", "푸른등대 기부장학사업 신규장학생 모집", "07.09"],
    ["장학", "학자금대출 사전 신청 일정 안내", "07.02"]
  ],
  career: [
    ["취업", "U7 대학연합 AI 활용 취업역량 강화 캠프", "07.19"],
    ["취업", "대학일자리플러스센터 진로상담 신청", "07.15"],
    ["창업", "2026 학생 창업 아이디어 경진대회", "07.11"],
    ["취업", "하계방학 현직자 직무 멘토링 안내", "07.04"]
  ]
};

const list = document.querySelector("[data-notice-list]");
const drawer = document.querySelector("[data-ai-drawer]");
const scrim = document.querySelector("[data-drawer-scrim]");
const toast = document.querySelector("[data-toast]");
let previousFocus = null;

function renderNotices(key) {
  list.innerHTML = notices[key].map(([tag, title, date]) => `
    <li><span class="tag">${tag}</span><a href="#" data-notice-title="${title}">${title}</a><time datetime="2026-${date.replace('.', '-')}">${date}</time></li>
  `).join("");
}

function openDrawer() {
  previousFocus = document.activeElement;
  scrim.hidden = false;
  drawer.removeAttribute("inert");
  drawer.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
  document.body.style.overflow = "hidden";
  drawer.querySelector("[data-ai-close]").focus();
}

function closeDrawer() {
  drawer.classList.remove("open");
  drawer.setAttribute("aria-hidden", "true");
  drawer.setAttribute("inert", "");
  scrim.hidden = true;
  document.body.style.overflow = "";
  previousFocus?.focus();
}

renderNotices("academic");

document.querySelectorAll("[data-tab]").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll("[data-tab]").forEach((tab) => {
      const active = tab === button;
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-selected", String(active));
    });
    renderNotices(button.dataset.tab);
  });
});

document.querySelectorAll("[data-ai-open]").forEach((button) => button.addEventListener("click", openDrawer));
document.querySelector("[data-ai-close]").addEventListener("click", closeDrawer);
scrim.addEventListener("click", closeDrawer);

document.querySelector("[data-attachment-toggle]").addEventListener("click", (event) => {
  const summary = document.querySelector("[data-attachment-summary]");
  summary.hidden = !summary.hidden;
  event.currentTarget.setAttribute("aria-expanded", String(!summary.hidden));
  event.currentTarget.textContent = summary.hidden ? "요약 보기" : "요약 닫기";
});

document.querySelector("[data-calendar]").addEventListener("click", () => {
  toast.hidden = false;
  window.setTimeout(() => { toast.hidden = true; }, 3200);
});

const searchOverlay = document.querySelector("[data-search-overlay]");
document.querySelector("[data-search-open]").addEventListener("click", () => {
  searchOverlay.hidden = false;
  searchOverlay.querySelector("input").focus();
});
document.querySelector("[data-search-close]").addEventListener("click", () => { searchOverlay.hidden = true; });

const menuButton = document.querySelector("[data-menu-toggle]");
const mobileNav = document.querySelector("[data-mobile-nav]");
menuButton.addEventListener("click", () => {
  mobileNav.hidden = !mobileNav.hidden;
  menuButton.setAttribute("aria-expanded", String(!mobileNav.hidden));
});

document.querySelector("[data-phone-form]").addEventListener("submit", (event) => {
  event.preventDefault();
  const value = document.querySelector("#phone-query").value.trim();
  const result = document.querySelector("[data-phone-result]");
  result.textContent = value ? `${value} 검색은 실제 연동 시 교내 전화번호 API를 사용합니다.` : "검색할 성명 또는 부서명을 입력해주세요.";
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (!searchOverlay.hidden) searchOverlay.hidden = true;
  if (drawer.classList.contains("open")) closeDrawer();
});
