const state = {
  searchSource: "all",
  lyricsSource: "auto",
  telegramUser: null,
  health: null,
};

function $(selector) {
  return document.querySelector(selector);
}

function formatDuration(seconds) {
  const value = Number(seconds || 0);
  const minutes = Math.floor(value / 60);
  const remainder = value % 60;
  return `${minutes}:${String(remainder).padStart(2, "0")}`;
}

function setActiveSegment(containerId, value, stateKey) {
  const container = document.getElementById(containerId);
  container.querySelectorAll(".segmented__item").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.value === value);
  });
  state[stateKey] = value;
}

function switchTab(tabName) {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.classList.toggle("is-active", tab.dataset.tab === tabName);
  });
  document.querySelectorAll(".panel").forEach((panel) => {
    panel.classList.toggle("is-active", panel.id === `tab-${tabName}`);
  });
}

async function request(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.detail || payload.message || "Запрос не выполнен.");
  }
  return payload;
}

function createTrackCard(track, { saveButton = false, openLyricsButton = false } = {}) {
  const template = document.getElementById("track-card-template");
  const node = template.content.firstElementChild.cloneNode(true);
  node.querySelector(".track-card__title").textContent = track.title;
  node.querySelector(".track-card__artist").textContent = track.artists || "Неизвестный артист";
  node.querySelector(".track-card__meta").textContent = [
    track.album || null,
    track.duration_seconds ? formatDuration(track.duration_seconds) : null,
  ].filter(Boolean).join(" • ");
  node.querySelector(".track-card__source").textContent = track.source;

  const cover = node.querySelector(".track-card__cover");
  if (track.cover_url) {
    cover.style.backgroundImage = `url(${track.cover_url})`;
    cover.style.backgroundSize = "cover";
    cover.style.backgroundPosition = "center";
  }

  const actions = node.querySelector(".track-card__actions");
  if (track.external_url) {
    const link = document.createElement("a");
    link.className = "ghost-button";
    link.href = track.external_url;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = "Открыть";
    actions.appendChild(link);
  }

  if (saveButton) {
    const save = document.createElement("button");
    save.type = "button";
    save.className = "ghost-button";
    save.textContent = "В библиотеку";
    save.addEventListener("click", async () => {
      await request("/api/library/tracks", {
        method: "POST",
        body: JSON.stringify({ ...track, bucket: "library" }),
      });
      await loadLibrary();
      save.textContent = "Сохранено";
      save.disabled = true;
    });
    actions.appendChild(save);
  }

  if (openLyricsButton) {
    const lyrics = document.createElement("button");
    lyrics.type = "button";
    lyrics.className = "ghost-button";
    lyrics.textContent = "Текст";
    lyrics.addEventListener("click", () => {
      switchTab("lyrics");
      $("#lyrics-query").value = `${track.title} ${track.artists}`.trim();
    });
    actions.appendChild(lyrics);
  }

  return node;
}

async function bootstrap() {
  const tg = window.Telegram?.WebApp;
  if (tg) {
    tg.ready();
    tg.expand();
    state.telegramUser = tg.initDataUnsafe?.user || null;
    if (state.telegramUser) {
      $("#user-greeting").textContent = `Привет, ${state.telegramUser.first_name || "друг"}`;
      try {
        await request("/api/session", {
          method: "POST",
          body: JSON.stringify({
            init_data: tg.initData || null,
            user: state.telegramUser,
          }),
        });
      } catch (error) {
        console.warn(error);
      }
    }
  }
  await loadHealth();
  await loadLibrary();
  await loadLiked();
}

async function loadHealth() {
  const payload = await request("/api/health");
  state.health = payload;
  $("#health-pill").textContent = payload.ok ? "Сервисы доступны" : "Есть проблемы";
  $("#status-card").textContent =
    `Приложение: ${payload.app_name}\n` +
    `Яндекс.Музыка: ${payload.sources.yandex ? "подключена" : "не подключена"}\n` +
    `YouTube: ${payload.sources.youtube ? "доступен" : "недоступен"}\n` +
    `Тексты песен: ${payload.sources.lyrics ? "включены" : "выключены"}\n\n` +
    `Библиотека: ${payload.stats.library_count}\n` +
    `Лайки: ${payload.stats.liked_count}\n` +
    `Пользователи Mini App: ${payload.stats.user_count}`;
}

async function performSearch(event) {
  event.preventDefault();
  const query = $("#search-query").value.trim();
  if (!query) return;
  $("#search-summary").textContent = "Ищу треки...";
  const payload = await request(`/api/search?q=${encodeURIComponent(query)}&source=${state.searchSource}&limit=20`);
  $("#search-summary").textContent = `Найдено: ${payload.total}`;
  const container = $("#search-results");
  container.innerHTML = "";
  payload.results.forEach((track) => {
    container.appendChild(createTrackCard(track, { saveButton: true, openLyricsButton: true }));
  });
}

async function performLyricsSearch(event) {
  event.preventDefault();
  const query = $("#lyrics-query").value.trim();
  if (!query) return;
  $("#lyrics-result").textContent = "Ищу текст...";
  const payload = await request(`/api/lyrics?q=${encodeURIComponent(query)}&source=${state.lyricsSource}`);
  if (!payload.found) {
    $("#lyrics-result").textContent = payload.error || "Текст не найден.";
    return;
  }
  const lyrics = payload.lyrics;
  $("#lyrics-result").textContent =
    `${lyrics.title}\n${lyrics.artists}\nИсточник: ${lyrics.source}\n\n${lyrics.text}`;
}

async function loadLibrary() {
  const payload = await request("/api/library?bucket=library&limit=100");
  const container = $("#library-results");
  container.innerHTML = "";
  if (!payload.tracks.length) {
    container.innerHTML = `<div class="lyrics-card empty-state">Библиотека пока пустая. Сохраните трек из поиска.</div>`;
    return;
  }
  payload.tracks.forEach((track) => container.appendChild(createTrackCard(track)));
}

async function loadLiked() {
  const payload = await request("/api/library?bucket=liked&limit=100");
  renderLikedTracks(payload.tracks);
}

function renderLikedTracks(tracks) {
  const container = $("#liked-results");
  container.innerHTML = "";
  if (!tracks.length) {
    container.innerHTML = `<div class="lyrics-card empty-state">Лайки еще не синхронизированы.</div>`;
    return;
  }
  tracks.forEach((track) => container.appendChild(createTrackCard(track)));
}

function renderLikedStats(result) {
  const stats = $("#liked-stats");
  const items = [
    ["Всего", result.total],
    ["Новых", result.new_count],
    ["Уже было", result.existing_count],
    ["Удалено", result.removed_count],
  ];
  stats.innerHTML = "";
  items.forEach(([label, value]) => {
    const box = document.createElement("div");
    box.className = "stat-box";
    box.innerHTML = `<span>${label}</span><strong>${value}</strong>`;
    stats.appendChild(box);
  });
}

async function syncLiked() {
  $("#liked-status-text").textContent = "Синхронизация запущена...";
  const payload = await request("/api/liked/sync", { method: "POST" });
  $("#liked-status-text").textContent = payload.message;
  if (payload.result) {
    renderLikedStats(payload.result);
  }
  renderLikedTracks(payload.tracks || []);
  await loadHealth();
}

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => switchTab(tab.dataset.tab));
  });

  document.querySelectorAll("#search-source .segmented__item").forEach((button) => {
    button.addEventListener("click", () => setActiveSegment("search-source", button.dataset.value, "searchSource"));
  });

  document.querySelectorAll("#lyrics-source .segmented__item").forEach((button) => {
    button.addEventListener("click", () => setActiveSegment("lyrics-source", button.dataset.value, "lyricsSource"));
  });

  $("#search-form").addEventListener("submit", (event) => {
    performSearch(event).catch((error) => {
      $("#search-summary").textContent = error.message;
    });
  });

  $("#lyrics-form").addEventListener("submit", (event) => {
    performLyricsSearch(event).catch((error) => {
      $("#lyrics-result").textContent = error.message;
    });
  });

  $("#liked-sync-button").addEventListener("click", () => {
    syncLiked().catch((error) => {
      $("#liked-status-text").textContent = error.message;
    });
  });

  bootstrap().catch((error) => {
    $("#health-pill").textContent = error.message;
  });
});
