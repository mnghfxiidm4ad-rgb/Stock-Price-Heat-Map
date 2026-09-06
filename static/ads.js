window.STOCKCHRONICLE_ADS = {
  enabled: false,
  client: "ca-pub-2075840815269276",
  slots: {
    mid: "",
    bottom: ""
  }
};

(function initAds() {
  const cfg = window.STOCKCHRONICLE_ADS || {};
  const wraps = document.querySelectorAll("[data-ad-place]");

  function showPlaceholder(box, place) {
    if (!box) return;
    box.classList.remove("ready");
    box.innerHTML = '<span class="ad-placeholder">Google広告枠（' + place + "）</span>";
  }

  function fill() {
    wraps.forEach((wrap) => {
      const place = wrap.getAttribute("data-ad-place") || "";
      const box = wrap.querySelector(".ad-box");
      const slotId = cfg.slots && cfg.slots[place];
      const clientOk = Boolean(
        cfg.enabled &&
        cfg.client &&
        String(cfg.client).startsWith("ca-pub-") &&
        String(cfg.client).indexOf("XXXX") < 0
      );
      if (!clientOk || !slotId) {
        showPlaceholder(box, place);
        return;
      }
      box.classList.add("ready");
      box.innerHTML =
        '<ins class="adsbygoogle" style="display:block" data-ad-client="' +
        cfg.client +
        '" data-ad-slot="' +
        slotId +
        '" data-ad-format="auto" data-full-width-responsive="true"></ins>';
    });
    document.querySelectorAll("ins.adsbygoogle").forEach(() => {
      try {
        (window.adsbygoogle = window.adsbygoogle || []).push({});
      } catch (err) {
        /* ignore */
      }
    });
  }

  const clientOk = Boolean(
    cfg.enabled &&
    cfg.client &&
    String(cfg.client).startsWith("ca-pub-") &&
    String(cfg.client).indexOf("XXXX") < 0
  );
  if (clientOk) {
    const s = document.createElement("script");
    s.async = true;
    s.crossOrigin = "anonymous";
    s.src =
      "https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=" +
      encodeURIComponent(cfg.client);
    s.addEventListener("load", fill);
    s.addEventListener("error", fill);
    document.head.appendChild(s);
  } else {
    fill();
  }
})();
