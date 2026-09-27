/* Awesome-UIE frontend.
 *
 * Pure client-side rendering: fetch papers.json, filter/sort in memory and
 * render cards. No framework, no build step.
 */
(function () {
  "use strict";

  var DATA_URL = "data/papers.json";
  var THEME_KEY = "uie-theme";
  var THEME_MODES = ["system", "light", "dark"];
  var THEME_LABELS = {
    system: "Theme: system (click to switch)",
    light: "Theme: light (click to switch)",
    dark: "Theme: dark (click to switch)",
  };
  var TYPE_ORDER = { Traditional: 0, DeepLearning: 1, Hybrid: 2 };
  var MAX_BAR_PX = 90; // max pixel height of a year bar (avoids WebKit %-height issues)

  var state = {
    all: [],
    venueNames: {},
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
        (paper.authors || []).join(" "),
        paper.doi || "",
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
    var fullName = state.venueNames[paper.venue];
    if (fullName && fullName !== paper.venue) {
      venue.title = fullName;
    }

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

    if (paper.authors && paper.authors.length) {
      var authors = document.createElement("p");
      authors.className = "card-authors";
      var shown = paper.authors.slice(0, 4).join(", ");
      if (paper.authors.length > 4) shown += " et al.";
      authors.textContent = shown;
      authors.title = paper.authors.join(", ");
      article.appendChild(authors);
    }

    if (paper.tldr) {
      var tldr = document.createElement("p");
      tldr.className = "card-tldr";
      tldr.textContent = paper.tldr;
      article.appendChild(tldr);
    }

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
    if (paper.doi) links.appendChild(externalLink("DOI", "https://doi.org/" + paper.doi));
    article.appendChild(links);

    return article;
  }

  function countBy(list, keyFn) {
    var counts = {};
    list.forEach(function (item) {
      var key = keyFn(item);
      if (key === null || key === undefined || key === "") return;
      counts[key] = (counts[key] || 0) + 1;
    });
    return counts;
  }

  function entriesFrom(counts, limit) {
    var entries = Object.keys(counts).map(function (key) {
      return { label: key, value: counts[key] };
    });
    entries.sort(function (a, b) {
      return b.value - a.value || a.label.localeCompare(b.label);
    });
    return limit ? entries.slice(0, limit) : entries;
  }

  function renderBarChart(container, entries) {
    container.textContent = "";
    if (!entries.length) return;
    var max = entries.reduce(function (current, entry) {
      return Math.max(current, entry.value);
    }, 0) || 1;
    entries.forEach(function (entry) {
      var row = document.createElement("div");
      row.className = "stat-row";

      var label = document.createElement("span");
      label.className = "stat-label";
      label.textContent = entry.label;
      label.title = entry.label;

      var track = document.createElement("span");
      track.className = "stat-track";
      var bar = document.createElement("span");
      bar.className = "stat-bar";
      bar.style.width = (entry.value / max) * 100 + "%";
      track.appendChild(bar);

      var value = document.createElement("span");
      value.className = "stat-value";
      value.textContent = entry.value;

      row.appendChild(label);
      row.appendChild(track);
      row.appendChild(value);
      container.appendChild(row);
    });
  }

  function renderYearChart(container, papers) {
    container.textContent = "";
    var counts = countBy(papers, function (paper) { return paper.year; });
    var years = Object.keys(counts).map(Number).sort(function (a, b) { return a - b; });
    if (!years.length) return;
    var max = years.reduce(function (current, year) {
      return Math.max(current, counts[year]);
    }, 0) || 1;
    years.forEach(function (year) {
      var column = document.createElement("div");
      column.className = "year-col";
      column.title = year + ": " + counts[year];

      var wrap = document.createElement("div");
      wrap.className = "year-bar-wrap";
      var bar = document.createElement("div");
      bar.className = "year-bar";
      // Use pixel heights: percentage heights inside a flex item are unreliable
      // in WebKit (the bars can collapse to zero).
      bar.style.height = Math.max(2, Math.round((counts[year] / max) * MAX_BAR_PX)) + "px";
      wrap.appendChild(bar);

      var label = document.createElement("span");
      label.className = "year-label";
      label.textContent = String(year).slice(2);

      column.appendChild(wrap);
      column.appendChild(label);
      container.appendChild(column);
    });
  }

  function renderStats(papers) {
    renderYearChart(el("chart-years"), papers);

    var typeCounts = countBy(papers, function (paper) { return paper.type; });
    var typeEntries = Object.keys(typeCounts)
      .sort(function (a, b) { return (TYPE_ORDER[a] || 0) - (TYPE_ORDER[b] || 0); })
      .map(function (type) { return { label: type, value: typeCounts[type] }; });
    // keep bar-chart scaling meaningful even though the order is fixed
    renderBarChart(el("chart-types"), typeEntries);

    var authorCounts = {};
    papers.forEach(function (paper) {
      (paper.authors || []).forEach(function (author) {
        authorCounts[author] = (authorCounts[author] || 0) + 1;
      });
    });
    renderBarChart(el("chart-authors"), entriesFrom(authorCounts, 8));

    var venueCounts = countBy(papers, function (paper) { return paper.venue; });
    renderBarChart(el("chart-venues"), entriesFrom(venueCounts, 8));
  }

  function render() {
    var filtered = sorted(state.all.filter(matches));
    el("result-count").textContent =
      filtered.length + " of " + state.all.length + " papers";
    el("empty").hidden = filtered.length > 0;
    el("stats").hidden = filtered.length === 0;
    renderStats(filtered);

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

    function getMode() {
      try { return localStorage.getItem(THEME_KEY) || "system"; } catch (e) { return "system"; }
    }

    function resolve(mode) {
      if (mode === "system") return media && media.matches ? "dark" : "light";
      return mode;
    }

    function apply(mode, persist) {
      document.documentElement.setAttribute("data-mode", mode);
      document.documentElement.setAttribute("data-theme", resolve(mode));
      if (persist) {
        try { localStorage.setItem(THEME_KEY, mode); } catch (e) {}
      }
      if (button) {
        var label = THEME_LABELS[mode] || THEME_LABELS.system;
        button.setAttribute("aria-label", label);
        button.title = label;
      }
    }

    apply(getMode(), false);

    if (button) {
      button.addEventListener("click", function () {
        var next = THEME_MODES[(THEME_MODES.indexOf(getMode()) + 1) % THEME_MODES.length];
        apply(next, true);
      });
    }

    // While in "system" mode, follow the OS preference live.
    if (media) {
      var onSystemChange = function () {
        if (getMode() === "system") apply("system", false);
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
        state.venueNames = data.venues || {};
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
