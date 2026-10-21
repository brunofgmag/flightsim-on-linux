const COPY_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/></svg>';
const DONE_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';
const RESET_AFTER_MS = 1800;

function copyWithSelection(text) {
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  const copied = document.execCommand("copy");
  area.remove();
  return copied;
}

async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      return copyWithSelection(text);
    }
  }
  return copyWithSelection(text);
}

function showState(button, label, icon) {
  button.innerHTML = icon;
  button.setAttribute("aria-label", label);
  button.title = label;
}

function addCopyButton(pre) {
  const block = document.createElement("div");
  block.className = "code-block";
  pre.replaceWith(block);
  block.appendChild(pre);

  const button = document.createElement("button");
  button.type = "button";
  button.className = "copy";
  showState(button, "Copy", COPY_ICON);
  button.addEventListener("click", async () => {
    const copied = await copyText(pre.innerText.replace(/\n+$/, ""));
    showState(button, copied ? "Copied" : "Copy failed: select the text instead", copied ? DONE_ICON : COPY_ICON);
    button.classList.toggle("done", copied);
    clearTimeout(button.reset);
    button.reset = setTimeout(() => {
      showState(button, "Copy", COPY_ICON);
      button.classList.remove("done");
    }, RESET_AFTER_MS);
  });
  block.appendChild(button);
}

document.querySelectorAll("main pre").forEach(addCopyButton);
