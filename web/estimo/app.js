// ¿Es timo? website: shrink screenshots in the browser, send them with the text as JSON, show
// the report. The report HTML comes from our server (only <b>, <i>, <code> survive there).
(() => {
  "use strict";
  const MAX_IMAGES = 8;
  const MAX_SIDE = 1600;
  const $ = (id) => document.getElementById(id);
  const form = $("form"), text = $("text"), files = $("files"), thumbs = $("thumbs");
  const go = $("go"), working = $("working"), error = $("error");
  const out = $("out"), result = $("result"), add = $("add"), again = $("again");
  const label = $("text-label");
  const exampleBtn = $("example"), exampleOut = $("example-out");
  const EN = document.documentElement.lang === "en";
  const S = EN ? {
    shot: "Screenshot ", remove: "Remove screenshot ", max: (n) => "At most " + n + " screenshots.",
    copy: "Tap to copy", empty: "Paste the listing or the conversation, or add screenshots.",
    failed: "Something went wrong. Please try again in a while.",
    offline: "Cannot reach the server. Please try again.",
    answer: "The advertiser's answer (analysed together with the rest)",
    first: "The listing and the conversation with the «landlord»",
    showExample: "👀 See an example", hideExample: "Hide the example",
  } : {
    shot: "Captura ", remove: "Quitar captura ", max: (n) => "Como mucho " + n + " capturas.",
    copy: "Toca para copiar", empty: "Pega el anuncio o la conversación, o añade capturas.",
    failed: "Algo ha fallado. Inténtalo de nuevo en un rato.",
    offline: "No hay conexión con el servidor. Inténtalo de nuevo.",
    answer: "La respuesta del anunciante (se analiza junto con lo anterior)",
    first: "El anuncio y la conversación con el «propietario»",
    showExample: "👀 Ver un ejemplo", hideExample: "Ocultar el ejemplo",
  };
  let images = []; // {blob, url}
  let token = null; // continue an analysed case with the advertiser's answer

  function showError(msg) { error.textContent = msg; error.hidden = !msg; }

  async function shrink(file) {
    let bitmap;
    try {
      bitmap = await createImageBitmap(file);
    } catch {
      return file; // the server re-encodes it or says it cannot read it
    }
    const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close && bitmap.close();
    return await new Promise((ok) => canvas.toBlob((b) => ok(b || file), "image/jpeg", 0.85));
  }

  function render() {
    thumbs.replaceChildren(...images.map((img, i) => {
      const box = document.createElement("div");
      box.className = "thumb";
      const el = document.createElement("img");
      el.src = img.url;
      el.alt = S.shot + (i + 1);
      const del = document.createElement("button");
      del.type = "button";
      del.textContent = "×";
      del.setAttribute("aria-label", S.remove + (i + 1));
      del.onclick = () => { URL.revokeObjectURL(img.url); images.splice(i, 1); render(); };
      box.append(el, del);
      return box;
    }));
  }

  async function addFiles(list) {
    showError("");
    for (const f of list) {
      if (!f.type.startsWith("image/")) continue;
      if (images.length >= MAX_IMAGES) { showError(S.max(MAX_IMAGES)); break; }
      const blob = await shrink(f);
      images.push({ blob, url: URL.createObjectURL(blob) });
    }
    render();
  }

  files.addEventListener("change", () => { addFiles(files.files); files.value = ""; });
  document.addEventListener("paste", (e) => {
    const list = [...(e.clipboardData?.items || [])]
      .filter((it) => it.kind === "file").map((it) => it.getAsFile()).filter(Boolean);
    if (list.length) { e.preventDefault(); addFiles(list); }
  });

  const b64 = (blob) => new Promise((ok, ko) => {
    const r = new FileReader();
    r.onload = () => ok(String(r.result).split(",", 2)[1]);
    r.onerror = ko;
    r.readAsDataURL(blob);
  });

  function clearInput() {
    text.value = "";
    images.forEach((i) => URL.revokeObjectURL(i.url));
    images = [];
    render();
  }

  function enableCopy(box) {
    box.querySelectorAll("code").forEach((c) => {
      c.title = S.copy;
      c.addEventListener("click", () => {
        navigator.clipboard?.writeText(c.textContent).then(() => {
          c.classList.add("copied");
          setTimeout(() => c.classList.remove("copied"), 1200);
        });
      });
    });
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    showError("");
    if (!text.value.trim() && !images.length) {
      showError(S.empty);
      return;
    }
    go.disabled = true;
    working.hidden = false;
    try {
      const body = { text: text.value, images: await Promise.all(images.map((i) => b64(i.blob))) };
      if (token) body.continue = token;
      const resp = await fetch("/api/analyze" + (EN ? "?lang=en" : ""), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      let data;
      try { data = await resp.json(); } catch { data = { ok: false }; }
      if (!data.ok) {
        showError(data.error || S.failed);
        if (resp.status === 410) token = null;
        return;
      }
      result.className = "card result" + (data.level ? " " + data.level : "");
      result.innerHTML = data.report;
      enableCopy(result);
      out.hidden = false;
      token = data.continue || null;
      add.hidden = !token;
      form.hidden = true;
      clearInput();
      out.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch {
      showError(S.offline);
    } finally {
      go.disabled = false;
      working.hidden = true;
    }
  });

  // The invented example: a saved report, no analysis spent. Fetched once, then toggled.
  let exampleLoaded = false;
  if (exampleBtn) exampleBtn.addEventListener("click", async () => {
    if (!exampleOut.hidden) {
      exampleOut.hidden = true;
      exampleBtn.textContent = S.showExample;
      exampleBtn.setAttribute("aria-expanded", "false");
      return;
    }
    showError("");
    if (!exampleLoaded) {
      try {
        const resp = await fetch("/api/example" + (EN ? "?lang=en" : ""));
        const data = await resp.json();
        if (!data.ok) throw new Error();
        $("example-case").textContent = data.case;
        const box = $("example-report");
        box.className = "card result " + data.level;
        box.innerHTML = data.report;
        enableCopy(box);
        exampleLoaded = true;
      } catch {
        showError(S.offline);
        return;
      }
    }
    exampleOut.hidden = false;
    exampleBtn.textContent = S.hideExample;
    exampleBtn.setAttribute("aria-expanded", "true");
    exampleOut.scrollIntoView({ behavior: "smooth", block: "start" });
  });

  add.addEventListener("click", () => {
    label.textContent = S.answer;
    form.hidden = false;
    text.focus();
    form.scrollIntoView({ behavior: "smooth", block: "start" });
  });

  again.addEventListener("click", () => {
    token = null;
    label.textContent = S.first;
    out.hidden = true;
    form.hidden = false;
    clearInput();
    showError("");
    text.focus();
  });
})();
