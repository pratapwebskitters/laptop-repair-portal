const form = document.querySelector("#estimate-decision");
const feedback = document.querySelector("#decision-feedback");
const buttons = form.querySelectorAll("button");

form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const fields = new FormData(form, event.submitter);
    buttons.forEach((button) => { button.disabled = true; });
    feedback.textContent = "";
    try {
        // A navigation POST from a no-referrer page can send a null Origin.
        const response = await fetch(form.action, {
            method: "POST",
            mode: "cors",
            headers: { Accept: "application/json" },
            body: fields,
        });
        if (response.ok) {
            window.location.reload();
            return;
        }
        if (response.status === 400) {
            const result = await response.json();
            feedback.textContent = result.error;
        } else {
            feedback.textContent = "Refresh the page and try again if this estimate is still available.";
        }
    } catch {
        feedback.textContent = "The decision could not be confirmed. Refresh the page before trying again.";
    }
    buttons.forEach((button) => { button.disabled = false; });
});

form.hidden = false;
