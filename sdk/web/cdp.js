/*!
 * DMAG CDP web SDK (v0.1) — page/track/identify, Segment-spec compatible.
 *
 * Configure before loading:
 *   window.CDP_CONFIG = {
 *     endpoint: "https://t.dmag.vn",   // CDP Core collector
 *     writeKey: "wk_dmag",             // public tenant key
 *     userToken: "<signed by Odoo>",   // only when the visitor is logged in
 *     page: {type: "course", course_id: 1, course_name: "...", lesson_id: 2},
 *     cartOrderId: 42,
 *   };
 *
 * Video progress (25/50/75/100%): YouTube embeds and HTML5 <video> (NAS
 * direct links). Google Drive previews expose no player API, so only
 * "Video Opened" and time spent on the lesson are recorded.
 */
(function (w, d) {
  "use strict";
  var cfg = w.CDP_CONFIG || {};
  if (!cfg.endpoint || !cfg.writeKey || w.cdp && w.cdp.__loaded) return;

  var COOKIE = "_cdp_aid";
  var queue = [];
  var timer = null;

  function uuid() {
    if (w.crypto && w.crypto.randomUUID) return w.crypto.randomUUID();
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0;
      return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
    });
  }

  function anonymousId() {
    var m = d.cookie.match(new RegExp("(?:^|; )" + COOKIE + "=([^;]+)"));
    var id = m ? decodeURIComponent(m[1]) : null;
    try { id = id || w.localStorage.getItem(COOKIE); } catch (e) { /* storage blocked */ }
    id = id || uuid();
    d.cookie = COOKIE + "=" + encodeURIComponent(id) + ";path=/;max-age=" + 63072000 + ";SameSite=Lax";
    try { w.localStorage.setItem(COOKIE, id); } catch (e) { /* ignore */ }
    return id;
  }

  var aid = anonymousId();

  function campaign() {
    var out = {}, p = new URLSearchParams(w.location.search);
    ["utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "fbclid", "zclid", "gclid"].forEach(function (k) {
      if (p.get(k)) out[k] = p.get(k);
    });
    return out;
  }

  function context() {
    return {
      page: { url: w.location.href, path: w.location.pathname, referrer: d.referrer, title: d.title },
      userAgent: w.navigator.userAgent,
      locale: w.navigator.language,
      library: { name: "dmag-cdp-js", version: "0.1.0" },
    };
  }

  function enqueue(type, props, event) {
    var msg = {
      type: type, messageId: uuid(), anonymousId: aid, timestamp: new Date().toISOString(),
      properties: props || {}, context: context(),
    };
    if (event) msg.event = event;
    if (cfg.userToken) msg.userToken = cfg.userToken;
    queue.push(msg);
    if (cfg.debug) w.console.log("[cdp]", type, event || "", msg.properties);
    if (queue.length >= 20) flush(); else if (!timer) timer = w.setTimeout(flush, 2000);
  }

  function flush(useBeacon) {
    if (timer) { w.clearTimeout(timer); timer = null; }
    if (!queue.length) return;
    var body = JSON.stringify({ writeKey: cfg.writeKey, batch: queue.splice(0, 100) });
    var url = cfg.endpoint.replace(/\/$/, "") + "/v1/collect";
    // text/plain keeps this a "simple" request: no CORS preflight, works with sendBeacon.
    if (useBeacon && w.navigator.sendBeacon) {
      w.navigator.sendBeacon(url, new Blob([body], { type: "text/plain" }));
    } else {
      w.fetch(url, { method: "POST", body: body, keepalive: true, headers: { "Content-Type": "text/plain" } })
        .catch(function () { /* best effort */ });
    }
  }

  var api = {
    __loaded: true,
    anonymousId: function () { return aid; },
    page: function (props) {
      var c = context().page, p = { url: c.url, path: c.path, title: c.title, referrer: c.referrer };
      var cp = campaign(); for (var k in cp) p[k] = cp[k];
      for (var k2 in (props || {})) p[k2] = props[k2];
      enqueue("page", p);
    },
    track: function (event, props) { enqueue("track", props, event); },
    identify: function () {
      if (!cfg.userToken) return;
      // The token is re-signed on every page render (it expires), so remember
      // the partner it identifies rather than the token itself.
      var who = cfg.userToken;
      try { who = w.atob(cfg.userToken.split(".")[0].replace(/-/g, "+").replace(/_/g, "/")).split(".")[0]; } catch (e) { /* keep token */ }
      var key = "_cdp_identified";
      try { if (w.sessionStorage.getItem(key) === who) return; } catch (e) { /* ignore */ }
      enqueue("identify", {});
      try { w.sessionStorage.setItem(key, who); } catch (e) { /* ignore */ }
    },
    flush: flush,
  };
  w.cdp = api;

  // ------------------------------------------------------------------ page context (Odoo eLearning / shop)
  var page = cfg.page || {};
  var lessonStart = Date.now();

  function lessonProps(extra) {
    var p = { course_id: page.course_id, course_name: page.course_name, lesson_id: page.lesson_id, lesson_name: page.lesson_name };
    for (var k in (extra || {})) p[k] = extra[k];
    return p;
  }

  // ------------------------------------------------------------------ video tracking
  var MILESTONES = [25, 50, 75, 100];
  var reached = {};

  function progress(key, provider, pct) {
    reached[key] = reached[key] || {};
    MILESTONES.forEach(function (m) {
      if (pct >= m - 1 && !reached[key][m]) {
        reached[key][m] = true;
        api.track("Video Progress", lessonProps({ milestone: m, provider: provider, video: key }));
      }
    });
  }

  function watchHtml5(video) {
    if (video.__cdp) return; video.__cdp = true;
    var key = video.currentSrc || video.src || "html5";
    video.addEventListener("play", function () {
      if (!reached[key]) api.track("Video Opened", lessonProps({ provider: "html5", video: key }));
      reached[key] = reached[key] || {};
    });
    video.addEventListener("timeupdate", function () {
      if (video.duration) progress(key, "html5", (video.currentTime / video.duration) * 100);
    });
  }

  function watchIframe(frame) {
    if (frame.__cdp) return; frame.__cdp = true;
    var src = frame.getAttribute("src") || "";
    if (/youtube(-nocookie)?\.com\/embed\//.test(src)) {
      if (!/enablejsapi=1/.test(src)) {
        frame.setAttribute("src", src + (src.indexOf("?") > -1 ? "&" : "?") + "enablejsapi=1");
      }
      var listen = function () {
        try { frame.contentWindow.postMessage(JSON.stringify({ event: "listening", id: src }), "*"); } catch (e) { /* ignore */ }
      };
      frame.addEventListener("load", listen); listen();
      api.track("Video Opened", lessonProps({ provider: "youtube", video: src }));
      frame.__cdpKey = src;
    } else if (/drive\.google\.com\//.test(src)) {
      api.track("Video Opened", lessonProps({ provider: "gdrive", video: src }));
    }
  }

  // YouTube iframe API "infoDelivery" messages carry currentTime/duration.
  w.addEventListener("message", function (e) {
    if (!/youtube(-nocookie)?\.com$/.test((e.origin || "").replace(/^https?:\/\//, "").replace(/^www\./, ""))) return;
    var data; try { data = typeof e.data === "string" ? JSON.parse(e.data) : e.data; } catch (err) { return; }
    var info = data && data.info;
    if (!info || !info.duration || info.currentTime == null) return;
    var frames = d.querySelectorAll("iframe");
    for (var i = 0; i < frames.length; i++) {
      if (frames[i].contentWindow === e.source) {
        progress(frames[i].__cdpKey || "youtube", "youtube", (info.currentTime / info.duration) * 100);
        return;
      }
    }
  });

  function scan() {
    Array.prototype.forEach.call(d.querySelectorAll("video"), watchHtml5);
    Array.prototype.forEach.call(d.querySelectorAll("iframe"), watchIframe);
  }

  // ------------------------------------------------------------------ boot
  api.identify();
  api.page(page.type ? { page_type: page.type } : {});
  if (page.type === "course" && page.course_id) api.track("Course Viewed", lessonProps());
  if (page.type === "lesson" && page.lesson_id) api.track("Lesson Viewed", lessonProps());
  if (/^\/shop\/(checkout|payment)/.test(w.location.pathname) && cfg.cartOrderId) {
    api.track("Checkout Started", { order_id: cfg.cartOrderId, amount: cfg.cartAmount });
  }
  scan();
  // eLearning fullscreen mode swaps lessons without a page load.
  if (w.MutationObserver) new w.MutationObserver(scan).observe(d.documentElement, { childList: true, subtree: true });

  w.addEventListener("pagehide", function () {
    if (page.type === "lesson" && page.lesson_id) {
      api.track("Lesson Time Spent", lessonProps({ seconds: Math.round((Date.now() - lessonStart) / 1000) }));
    }
    flush(true);
  });
  d.addEventListener("visibilitychange", function () { if (d.visibilityState === "hidden") flush(true); });
})(window, document);
