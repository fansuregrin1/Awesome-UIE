/* Awesome-UIE frontend.
 *
 * Pure client-side rendering: fetch papers.json, filter/sort in memory and
 * render cards. No framework, no build step.
 */
(function () {
  "use strict";

  var DATA_URL = "data/papers.json";
  var THEME_KEY = "uie-theme";
  var TYPE_ORDER = { Traditional: 0, DeepLearning: 1, Hybrid: 2 };

  var state = {
    all: [],
    search: "",
    year: "",
    type: "",
    venue: "",
    tag: "",
    hasCode: false,
    sort: "year-desc",
  };

  function el(id) {
    return document.getElementById(id);
  }

  function byYearThenTitle(a, b) {
    return (
      TYPE_ORDER[a.type] - TYPE_ORDER[b.type] ||
      a.venue.localeCompare(b.venue) ||
      a.title.localeCompare(b.title)
    );
  }

  function populateSelect(id, values) {
    var select = el(id);
    values.forEach(function (value) {
      var option = document.createElement("option");
      option.value = String(value);
      option.textContent = String(value);
      select.appendChild(option);
    });
  }

  function unique(values) {
    var seen = {};
    var out = [];
    values.forEach(function (value) {
      if (!seen[value]) {
        seen[value] = true;
        out.push(value);
      }
    });
    return out;
  }

  function buildFilters() {
    var years = unique(state.all.map(function (p) { return p.year; })).sort(function (a, b) { return b - a; });
    var types = unique(state.all.map(function (p) { return p.type; })).sort(function (a, b) {
      return (TYPE_ORDER[a] || 0) - (TYPE_ORDER[b] || 0);
    });
    var venues = unique(state.all.map(function (p) { return p.venue; })).sort();
    var tags = unique(
      state.all.reduce(function (acc, p) { return acc.concat(p.tags || []); }, [])
    ).sort();

    populateSelect("year", years);
    populateSelect("type", types);
    populateSelect("venue", venues);
    populateSelect("tag", tags);
  }

  function matches(paper) {
    if (state.year && String(paper.year) !== state.year) return false;
    if (state.type && paper.type !== state.type) return false;
    if (state.venue && paper.venue !== state.venue) return false;
    if (state.tag && (paper.tags || []).indexOf(state.tag) === -1) return false;
    if (state.hasCode && !paper.code) return false;
    if (state.search) {
      var haystack = [
        paper.title,
        paper.venue,
        String(paper.year),
        (paper.tags || []).join(" "),
      ]
        .join(" ")
        .toLowerCase();
      if (haystack.indexOf(state.search) === -1) return false;
    }
    return true;
  }

  function sorted(list) {
    var comparators = {
      "year-desc": function (a, b) { return b.year - a.year || byYearThenTitle(a, b); },
      "year-asc": function (a, b) { return a.year - b.year || byYearThenTitle(a, b); },
      venue: function (a, b) { return a.venue.localeCompare(b.venue) || b.year - a.year || a.title.localeCompare(b.title); },
      type: function (a, b) { return (TYPE_ORDER[a.type] - TYPE_ORDER[b.type]) || b.year - a.year || a.title.localeCompare(b.title); },
    };
    return list.slice().sort(comparators[state.sort] || comparators["year-desc"]);
  }

  function externalLink(text, href, className) {
    var a = document.createElement("a");
    a.href = href;
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = text;
    if (className) a.className = className;
    return a;
  }

  function card(paper) {
    var article = document.createElement("article");
    article.className = "card";

    var top = document.createElement("div");
    top.className = "card-top";

    var venue = document.createElement("span");
    venue.className = "badge venue";
    venue.textContent = paper.venue;

    var type = document.createElement("span");
    type.className = "badge type type-" + paper.type;
    type.textContent = paper.type;

    var year = document.createElement("span");
    year.className = "year";
    year.textContent = paper.year;

    top.appendChild(venue);
    top.appendChild(type);
    top.appendChild(year);

    var title = document.createElement("h2");
    title.className = "card-title";
    title.appendChild(externalLink(paper.title, paper.url));

    article.appendChild(top);
    article.appendChild(title);

    if (paper.tags && paper.tags.length) {
      var tags = document.createElement("div");
      tags.className = "tags";
      paper.tags.forEach(function (value) {
        var tag = document.createElement("span");
        tag.className = "tag";
        tag.textContent = value;
        tags.appendChild(tag);
      });
      article.appendChild(tags);
    }

    var links = document.createElement("div");
    links.className = "card-links";
    links.appendChild(externalLink("Paper", paper.url));
    if (paper.code) links.appendChild(externalLink("Code", paper.code));
    if (paper.project) links.appendChild(externalLink("Project", paper.project));
    article.appendChild(links);

    return article;
  }

  function render() {
    var filtered = sorted(state.all.filter(matches));
    el("result-count").textContent =
      filtered.length + " of " + state.all.length + " papers";
    el("empty").hidden = filtered.length > 0;

    var container = el("results");
    container.textContent = "";
    var fragment = document.createDocumentFragment();
    filtered.forEach(function (paper) {
      fragment.appendChild(card(paper));
    });
    container.appendChild(fragment);
  }

  function bind() {
    var timer;
    el("search").addEventListener("input", function (event) {
      var value = event.target.value.trim().toLowerCase();
      clearTimeout(timer);
      timer = setTimeout(function () {
        state.search = value;
        render();
      }, 150);
    });

    ["year", "type", "venue", "tag", "sort"].forEach(function (id) {
      el(id).addEventListener("change", function (event) {
        state[id] = event.target.value;
        render();
      });
    });

    el("has-code").addEventListener("change", function (event) {
      state.hasCode = event.target.checked;
      render();
    });

    el("reset").addEventListener("click", function () {
      state.search = "";
      state.year = "";
      state.type = "";
      state.venue = "";
      state.tag = "";
      state.hasCode = false;
      state.sort = "year-desc";
      el("search").value = "";
      el("year").value = "";
      el("type").value = "";
      el("venue").value = "";
      el("tag").value = "";
      el("has-code").checked = false;
      el("sort").value = "year-desc";
      render();
    });
  }

  function fail(message) {
    var empty = el("empty");
    empty.hidden = false;
    empty.textContent = message;
    el("result-count").textContent = "";
  }

  function setupTheme() {
    var button = el("theme-toggle");
    var media = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

    function storedTheme() {
      try { return localStorage.getItem(THEME_KEY); } catch (e) { return null; }
    }

    function apply(theme, persist) {
      document.documentElement.setAttribute("data-theme", theme);
      if (persist) {
        try { localStorage.setItem(THEME_KEY, theme); } catch (e) {}
      }
      if (button) {
        var dark = theme === "dark";
        button.textContent = dark ? "\u2600\uFE0F" : "\uD83C\uDF19";
        button.setAttribute("aria-label", dark ? "Switch to light mode" : "Switch to dark mode");
        button.title = dark ? "Switch to light mode" : "Switch to dark mode";
      }
    }

    apply(document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light", false);

    if (button) {
      button.addEventListener("click", function () {
        var next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
        apply(next, true);
      });
    }

    // Keep following the system preference until the user makes a choice.
    if (media) {
      var onSystemChange = function (event) {
        if (!storedTheme()) apply(event.matches ? "dark" : "light", false);
      };
      if (media.addEventListener) media.addEventListener("change", onSystemChange);
      else if (media.addListener) media.addListener(onSystemChange);
    }
  }

  function init() {
    fetch(DATA_URL, { cache: "no-cache" })
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      })
      .then(function (data) {
        state.all = data.papers || [];
        el("total-count").textContent = state.all.length;
        buildFilters();
        bind();
        render();
      })
      .catch(function (error) {
        fail("Failed to load " + DATA_URL + ": " + error.message);
      });
  }

  document.addEventListener("DOMContentLoaded", function () {
    setupTheme();
    init();
  });
})();
