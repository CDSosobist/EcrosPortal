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


    // --- вложения: предпросмотр выбранных файлов и проверка лимитов ----------
    function formatSize(bytes) {
        if (bytes >= 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1).replace(".", ",") + " МБ";
        if (bytes >= 1024) return Math.round(bytes / 1024) + " КБ";
        return bytes + " Б";
    }

    document.querySelectorAll("input[type=file][data-file-max-mb]").forEach((input) => {
        const maxMb = parseFloat(input.getAttribute("data-file-max-mb") || "10");
        const maxCount = parseInt(input.getAttribute("data-file-max-count") || "0", 10);
        const list = input.closest(".file-drop")?.querySelector("[data-file-list]");
        if (!list) return;

        input.addEventListener("change", () => {
            list.textContent = "";
            const files = Array.from(input.files || []);
            files.forEach((file) => {
                const tooBig = file.size > maxMb * 1024 * 1024;
                const row = document.createElement("div");
                row.className = "file-drop__item" + (tooBig ? " file-drop__item--error" : "");
                const name = document.createElement("span");
                name.className = "file-drop__name";
                name.textContent = file.name;
                const size = document.createElement("span");
                size.className = "file-drop__size";
                size.textContent = tooBig ? formatSize(file.size) + " — больше " + maxMb + " МБ" : formatSize(file.size);
                row.appendChild(name);
                row.appendChild(size);
                list.appendChild(row);
            });
            if (maxCount && files.length > maxCount) {
                const row = document.createElement("div");
                row.className = "file-drop__item file-drop__item--error";
                row.textContent = "Выбрано " + files.length + " файлов, за раз можно не больше " + maxCount + ".";
                list.appendChild(row);
            }
        });
    });

    function getCookie(name) {
        const match = document.cookie.match(new RegExp("(^|;\\s*)" + name + "=([^;]*)"));
        return match ? decodeURIComponent(match[2]) : "";
    }
})();
