const trackingUrl = document.querySelector("#tracking-url");
const copyResult = document.querySelector("#copy-result");

document.querySelector("#copy-link").addEventListener("click", async () => {
    try {
        await navigator.clipboard.writeText(trackingUrl.value);
        copyResult.textContent = "Tracking link copied.";
    } catch {
        trackingUrl.select();
        copyResult.textContent = "Link selected. Use your device’s copy command.";
    }
});

document.querySelector("#rotate-link").addEventListener("submit", (event) => {
    if (!window.confirm("Replace this tracking link? The previous link will stop working.")) {
        event.preventDefault();
    }
});
