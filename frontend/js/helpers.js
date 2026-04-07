/**
 * Render a status message inside the given container element.
 *
 * @param {HTMLElement} element - The container whose innerHTML will be replaced.
 * @param {string}      text    - Message text (may contain HTML for line breaks etc.).
 * @param {string}      type    - CSS modifier class: "success" | "error" | "info".
 *
 * Error messages are automatically cleared after 5 seconds so they don't linger
 * on screen.
 */
export function showMessage(element, text, type) {
    element.innerHTML = `<div class="status-message ${type}">${text}</div>`;
    if (type === "error") {
        setTimeout(() => {
            element.innerHTML = "";
        }, 5000);
    }
}

/**
 * Uppercase the first character of a string.
 * Used to format raw category keys (e.g. "other") into display labels when
 * CATEGORY_LABELS does not contain an explicit override for that key.
 */
export function capitalize(value) {
    return value.charAt(0).toUpperCase() + value.slice(1);
}
