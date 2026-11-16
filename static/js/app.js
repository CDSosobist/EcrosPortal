/* Портал ЭКРОС — минимальный клиентский скрипт (без зависимостей). */
(function () {
    "use strict";

    // Мобильное меню
    const toggle = document.querySelector("[data-sidebar-toggle]");
    const sidebar = document.querySelector(".sidebar");
    if (toggle && sidebar) {
        toggle.addEventListener("click", () => sidebar.classList.toggle("is-open"));
        document.addEventListener("click", (event) => {
            if (!sidebar.contains(event.target) && !toggle.contains(event.target)) {
                sidebar.classList.remove("is-open");
            }
        });
    }

    // Автоскрытие flash-сообщений
    document.querySelectorAll("[data-autohide]").forEach((el) => {
        setTimeout(() => {
            el.style.transition = "opacity .4s ease";
            el.style.opacity = "0";
            setTimeout(() => el.remove(), 420);
        }, 6000);
    });

    // Подтверждение опасных действий
    document.querySelectorAll("form[data-confirm]").forEach((form) => {
        form.addEventListener("submit", (event) => {
            if (!window.confirm(form.getAttribute("data-confirm"))) {
                event.preventDefault();
            }
        });
    });

    // Модальные окна на <dialog>
    document.querySelectorAll("[data-open-dialog]").forEach((button) => {
        button.addEventListener("click", () => {
            const dialog = document.getElementById(button.getAttribute("data-open-dialog"));
            if (dialog && dialog.showModal) dialog.showModal();
        });
    });
    document.querySelectorAll("[data-close-dialog]").forEach((button) => {
        button.addEventListener("click", () => {
            const dialog = button.closest("dialog");
            if (dialog) dialog.close();
        });
    });

    // Живой подсчёт дней в форме заявления на отпуск
    const startInput = document.querySelector("[data-vacation-start]");
    const endInput = document.querySelector("[data-vacation-end]");
    const counter = document.querySelector("[data-vacation-days]");
    const holidaysScript = document.getElementById("holidays-data");
    let holidays = new Set();
    if (holidaysScript) {
        try {
            holidays = new Set(JSON.parse(holidaysScript.textContent || "[]"));
        } catch (err) {
            holidays = new Set();
        }
    }

    function isoDate(date) {
        return date.toISOString().slice(0, 10);
    }

    function recalcDays() {
        if (!startInput || !endInput || !counter) return;
        const start = startInput.value ? new Date(startInput.value + "T00:00:00") : null;
        const end = endInput.value ? new Date(endInput.value + "T00:00:00") : null;
        if (!start || !end || isNaN(start) || isNaN(end) || end < start) {
            counter.textContent = "—";
            return;
        }
        let total = 0;
        const cursor = new Date(start);
        while (cursor <= end) {
            if (!holidays.has(isoDate(cursor))) total += 1;
            cursor.setDate(cursor.getDate() + 1);
        }
        counter.textContent = total ? total + " календарных дн." : "0 дней";
    }
    if (startInput && endInput) {
        startInput.addEventListener("change", recalcDays);
        endInput.addEventListener("change", recalcDays);
        if (startInput.value && !endInput.value) {
            endInput.value = startInput.value;
        }
        recalcDays();
    }

    // Отметить уведомления прочитанными (fetch без перезагрузки)
    document.querySelectorAll("[data-mark-read]").forEach((button) => {
        button.addEventListener("click", async () => {
            const id = button.getAttribute("data-mark-read");
            const body = new URLSearchParams({ id: id });
            await fetch(button.getAttribute("data-url"), {
                method: "POST",
                headers: {
                    "X-CSRFToken": getCookie("csrftoken"),
                    "X-Requested-With": "XMLHttpRequest",
                },
                body,
            }).catch(() => null);
            const row = button.closest("[data-notification]");
            if (row) row.classList.remove("is-unread");
            button.remove();
        });
    });

    function getCookie(name) {
        const match = document.cookie.match(new RegExp("(^|;\\s*)" + name + "=([^;]*)"));
        return match ? decodeURIComponent(match[2]) : "";
    }
})();
