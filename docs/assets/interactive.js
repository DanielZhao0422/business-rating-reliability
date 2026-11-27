const form = document.getElementById("shrinkage");
const rating = document.getElementById("rating");
const reviews = document.getElementById("reviews");
const presets = document.getElementById("review-presets");
const weightBar = form.querySelector(".weight-bar");
const reference = Number(form.dataset.reference);
const strength = Number(form.dataset.strength);

function calculate() {
  if (!form.reportValidity()) return;
  const raw = rating.valueAsNumber;
  const count = reviews.valueAsNumber;
  const sellerWeight = count / (count + strength);
  const adjusted = sellerWeight * raw + (1 - sellerWeight) * reference;
  document.getElementById("demo-raw").textContent = raw.toFixed(2);
  document.getElementById("demo-adjusted").textContent = adjusted.toFixed(2);
  weightBar.style.setProperty("--seller-share", `${sellerWeight * 100}%`);
  document.getElementById("seller-weight").textContent = `${(sellerWeight * 100).toFixed(1)}%`;
  document.getElementById("reference-weight").textContent = `${((1 - sellerWeight) * 100).toFixed(1)}%`;
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  calculate();
});
form.addEventListener("reset", () => setTimeout(calculate, 0));

for (const button of presets.querySelectorAll("[data-reviews]")) {
  button.addEventListener("click", () => {
    reviews.value = button.dataset.reviews;
    calculate();
  });
}
presets.hidden = false;

const masthead = document.querySelector(".masthead");
new ResizeObserver(() => {
  document.documentElement.style.setProperty("--masthead-height", `${masthead.offsetHeight}px`);
}).observe(masthead);
