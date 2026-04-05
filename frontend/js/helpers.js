export function showMessage(element, text, type) {
    element.innerHTML = `<div class="status-message ${type}">${text}</div>`;
    if (type === "error") {
        setTimeout(() => {
            element.innerHTML = "";
        }, 5000);
    }
}

export function capitalize(value) {
    return value.charAt(0).toUpperCase() + value.slice(1);
}
