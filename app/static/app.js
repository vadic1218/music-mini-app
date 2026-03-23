const state = {
  searchSource: "all",
  lyricsSource: "auto",
  telegramUser: null,
  health: null,
  currentTrack: null,
  queue: [],
};

function $(selector) {
  return document.querySelector(selector);
}

function formatDuration(seconds) {
  const value = Number(seconds || 0);
  const minutes = Math.floor(value / 60);
  const remainder = Math.floor(value % 60);
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

function trackKey(track) {
  return `${track.source || "unknown"}:${track.source_track_id || track.external_url || track.title || "track"}`;
}

function updatePlayerUi() {
  const audio = $("#audio-player");
  const track = state.currentTrack;
  const hasTrack = Boolean(track);

  $("#player-title").textContent = hasTrack ? track.title : "Ничего не играет";
  $("#player-artist").textContent = hasTrack
    ? (track.artists || "Неизвестный артист")
    : "Выберите трек в поиске, лайках или библиотеке.";
  $("#player-current-time").textContent = formatDuration(audio.currentTime || 0);
  $("#player-duration").textContent = formatDuration(hasTrack ? (audio.duration || track.duration_seconds || 0) : 0);
  $("#player-seek").value = hasTrack && audio.duration ? Math.round((audio.currentTime / audio.duration) * 100) : 0;
  $("#player-seek").disabled = !hasTrack;

  const cover = $("#player-cover");
  if (hasTrack && track.cover_url) {
    cover.style.backgroundImage = `url(${track.cover_url})`;
    cover.style.backgroundSize = "cover";
    cover.style.backgroundPosition = "center";
  } else {
    cover.style.backgroundImage = "";
  }

  const playing = hasTrack && !audio.paused && !audio.ended;
  $("#player-toggle").textContent = hasTrack ? (playing ? "Пауза" : "Слушать") : "Слушать";
  $("#player-prev").disabled = !hasTrack;
  $("#player-next").disabled = !hasTrack && state.queue.length === 0;

  $("#player-dock").classList.toggle("is-hidden", !hasTrack);
  $("#player-dock-title").textContent = hasTrack ? track.title : "Ничего не играет";
  $("#player-dock-subtitle").textContent = hasTrack ? (track.artists || "Неизвестный артист") : "Выберите трек";
  $("#player-dock-toggle").textContent = playing ? "⏸" : "▶";
}

function renderQueue() {
  const container = $("#queue-list");
  container.innerHTML = "";
  if (!state.queue.length) {
    container.innerHTML = '<div class="lyrics-card empty-state">Очередь пуста. Добавьте треки кнопкой "Слушать следующим".</div>';
    return;
  }

  const template = $("#queue-item-template");
  state.queue.forEach((track, index) => {
    const node = template.content.firstElementChild.cloneNode(true);
    node.querySelector(".queue-item__title").textContent = `${index + 1}. ${track.title}`;
    node.querySelector(".queue-item__artist").textContent = track.artists || "Неизвестный артист";
    node.querySelector(".queue-item__body").addEventListener("click", () => {
      const [selected] = state.queue.splice(index, 1);
      renderQueue();
      playTrack(selected).catch((error) => {
        $("#search-summary").textContent = error.message;
      });
    });
    node.querySelector(".queue-item__remove").addEventListener("click", () => {
      state.queue.splice(index, 1);
      renderQueue();
      updatePlayerUi();
    });
    container.appendChild(node);
  });
}

async function fetchPlaybackUrl(track) {
  const payload = await request("/api/playback-url", {
    method: "POST",
    body: JSON.stringify(track),
  });
  return payload.stream_url;
}

async function playTrack(track) {
  const audio = $("#audio-player");
  const streamUrl = await fetchPlaybackUrl(track);
  state.currentTrack = { ...track, stream_url: streamUrl };
  audio.src = streamUrl;
  audio.currentTime = 0;
  await audio.play();
  switchTab("player");
  updatePlayerUi();
}

function enqueueTrack(track, { next = false } = {}) {
  const key = trackKey(track);
  const currentKey = state.currentTrack ? trackKey(state.currentTrack) : null;
  if (currentKey === key || state.queue.some((item) => trackKey(item) === key)) {
    renderQueue();
    updatePlayerUi();
    return;
  }
  if (next) {
    state.queue.unshift(track);
  } else {
    state.queue.push(track);
  }
  renderQueue();
  updatePlayerUi();
}

async function playNextTrack() {
  const nextTrack = state.queue.shift();
  renderQueue();
  if (!nextTrack) {
    const audio = $("#audio-player");
    audio.pause();
    audio.removeAttribute("src");
    audio.load();
    state.currentTrack = null;
    updatePlayerUi();
    return;
  }
  await playTrack(nextTrack);
}

function playPreviousTrack() {
  const audio = $("#audio-player");
  audio.currentTime = 0;
  updatePlayerUi();
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

  const play = document.createElement("button");
  play.type = "button";
  play.className = "primary-button";
  play.textContent = "Слушать";
  play.addEventListener("click", () => {
    playTrack(track).catch((error) => {
      $("#search-summary").textContent = error.message;
    });
  });
  actions.appendChild(play);

  const enqueue = document.createElement("button");
  enqueue.type = "button";
  enqueue.className = "ghost-button";
  enqueue.textContent = "Слушать следующим";
  enqueue.addEventListener("click", () => enqueueTrack(track, { next: true }));
  actions.appendChild(enqueue);

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
  renderQueue();
  updatePlayerUi();
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
  if (!query) {
    return;
  }
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
  if (!query) {
    return;
  }
  $("#lyrics-result").textContent = "Ищу текст...";
  const payload = await request(`/api/lyrics?q=${encodeURIComponent(query)}&source=${state.lyricsSource}`);
  if (!payload.found) {
    $("#lyrics-result").textContent = payload.error || "Текст не найден.";
    return;
  }
  const lyrics = payload.lyrics;
  $("#lyrics-result").textContent = `${lyrics.title}\n${lyrics.artists}\nИсточник: ${lyrics.source}\n\n${lyrics.text}`;
}

async function loadLibrary() {
  const payload = await request("/api/library?bucket=library&limit=100");
  const container = $("#library-results");
  container.innerHTML = "";
  if (!payload.tracks.length) {
    container.innerHTML = '<div class="lyrics-card empty-state">Библиотека пока пустая. Сохраните трек из поиска.</div>';
    return;
  }
  payload.tracks.forEach((track) => container.appendChild(createTrackCard(track, { openLyricsButton: true })));
}

async function loadLiked() {
  const payload = await request("/api/library?bucket=liked&limit=100");
  renderLikedTracks(payload.tracks);
}

function renderLikedTracks(tracks) {
  const container = $("#liked-results");
  container.innerHTML = "";
  if (!tracks.length) {
    container.innerHTML = '<div class="lyrics-card empty-state">Лайки еще не синхронизированы.</div>';
    return;
  }
  tracks.forEach((track) => container.appendChild(createTrackCard(track, { openLyricsButton: true })));
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

  $("#player-toggle").addEventListener("click", async () => {
    const audio = $("#audio-player");
    if (!state.currentTrack && state.queue.length) {
      await playNextTrack();
      return;
    }
    if (!state.currentTrack) {
      switchTab("search");
      return;
    }
    if (audio.paused) {
      await audio.play();
    } else {
      audio.pause();
    }
    updatePlayerUi();
  });

  $("#player-prev").addEventListener("click", () => {
    playPreviousTrack();
  });

  $("#player-next").addEventListener("click", () => {
    playNextTrack().catch((error) => {
      $("#search-summary").textContent = error.message;
    });
  });

  $("#queue-clear").addEventListener("click", () => {
    state.queue = [];
    renderQueue();
    updatePlayerUi();
  });

  $("#player-dock-open").addEventListener("click", () => {
    switchTab("player");
  });

  $("#player-dock-toggle").addEventListener("click", () => {
    $("#player-toggle").click();
  });

  $("#player-dock-next").addEventListener("click", () => {
    $("#player-next").click();
  });

  $("#player-seek").addEventListener("input", (event) => {
    const audio = $("#audio-player");
    if (!audio.duration) {
      return;
    }
    audio.currentTime = audio.duration * (Number(event.target.value || 0) / 100);
    updatePlayerUi();
  });

  $("#audio-player").addEventListener("timeupdate", updatePlayerUi);
  $("#audio-player").addEventListener("loadedmetadata", updatePlayerUi);
  $("#audio-player").addEventListener("play", updatePlayerUi);
  $("#audio-player").addEventListener("pause", updatePlayerUi);
  $("#audio-player").addEventListener("ended", () => {
    playNextTrack().catch((error) => {
      $("#search-summary").textContent = error.message;
    });
  });

  bootstrap().catch((error) => {
    $("#health-pill").textContent = error.message;
  });
});
