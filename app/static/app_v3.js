const state = {
  searchSource: "all",
  lyricsSource: "auto",
  telegramUser: null,
  telegramUserId: 0,
  health: null,
  accessStatus: null,
  currentTrack: null,
  currentCollection: [],
  queue: [],
  libraryTracks: [],
  downloadedTracks: [],
  activeTab: "search",
  playbackCache: {},
  shuffle: false,
  repeatMode: "off",
};

const STORAGE_KEYS = {
  queue: "ksb-mini-app-queue-v3",
  currentTrack: "ksb-mini-app-current-track-v3",
  shuffle: "ksb-mini-app-shuffle-v3",
  repeatMode: "ksb-mini-app-repeat-mode-v3",
  userId: "ksb-mini-app-user-id-v3",
  librarySnapshot: "ksb-mini-app-library-v4",
  downloadsSnapshot: "ksb-mini-app-downloads-v4",
};

function hasAccess() {
  const accessType = state.accessStatus?.access_type;
  return accessType === "premium" || accessType === "admin";
}

function ensureAccess() {
  if (hasAccess()) return true;
  switchTab("status");
  throw new Error("Для использования Mini App нужен промокод или активная подписка.");
}

function $(selector) {
  return document.querySelector(selector);
}

function currentUserId() {
  const value = Number(
    state.telegramUserId || state.telegramUser?.id || localStorage.getItem(STORAGE_KEYS.userId) || 0
  );
  return Number.isFinite(value) ? value : 0;
}

function ensureUserId() {
  const userId = currentUserId();
  if (!userId) {
    throw new Error("Mini App не получил Telegram user id. Закройте и заново откройте приложение из бота.");
  }
  return userId;
}

function formatDuration(seconds) {
  const value = Number(seconds || 0);
  const minutes = Math.floor(value / 60);
  const remainder = Math.floor(value % 60);
  return `${minutes}:${String(remainder).padStart(2, "0")}`;
}

function trackKey(track) {
  return `${track.source || "unknown"}:${track.source_track_id || track.external_url || track.title || "track"}`;
}

function getLibraryMirror(track) {
  return state.libraryTracks.find((item) => trackKey(item) === trackKey(track)) || null;
}

function isTrackDownloaded(track) {
  return Boolean(track.download_requested_at || getLibraryMirror(track)?.download_requested_at);
}

function downloadedLabel(track) {
  return isTrackDownloaded(track) ? "скачан" : "не скачан";
}

function showFatalError(message) {
  const text = String(message || "Ошибка интерфейса.");
  if ($("#health-pill")) $("#health-pill").textContent = text;
  if ($("#status-card")) {
    $("#status-card").textContent = `Mini App остановился с ошибкой:\n${text}`;
  }
  if ($("#search-summary")) $("#search-summary").textContent = text;
}

function setStatusText(message, target = "search") {
  const text = String(message || "");
  const map = {
    search: "#search-summary",
    library: "#library-summary",
    downloads: "#downloads-summary",
    "library-sync": "#library-sync-status",
  };
  const selector = map[target] || map.search;
  const node = $(selector);
  if (node) node.textContent = text;
}

function setActiveSegment(containerId, value, stateKey) {
  const container = document.getElementById(containerId);
  if (!container) return;
  container.querySelectorAll(".segmented__item").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.value === value);
  });
  state[stateKey] = value;
}

function persistPlayerState() {
  try {
    localStorage.setItem(STORAGE_KEYS.queue, JSON.stringify(state.queue));
    localStorage.setItem(STORAGE_KEYS.shuffle, JSON.stringify(state.shuffle));
    localStorage.setItem(STORAGE_KEYS.repeatMode, state.repeatMode);
    if (state.currentTrack) {
      localStorage.setItem(STORAGE_KEYS.currentTrack, JSON.stringify(state.currentTrack));
    } else {
      localStorage.removeItem(STORAGE_KEYS.currentTrack);
    }
    if (currentUserId()) {
      localStorage.setItem(STORAGE_KEYS.userId, String(currentUserId()));
    }
  } catch (error) {
    console.warn(error);
  }
}

function persistLibrarySnapshot() {
  try {
    localStorage.setItem(STORAGE_KEYS.librarySnapshot, JSON.stringify(state.libraryTracks || []));
    localStorage.setItem(STORAGE_KEYS.downloadsSnapshot, JSON.stringify(state.downloadedTracks || []));
  } catch (error) {
    console.warn(error);
  }
}

function restoreLibrarySnapshot() {
  try {
    const librarySnapshot = JSON.parse(localStorage.getItem(STORAGE_KEYS.librarySnapshot) || "[]");
    const downloadsSnapshot = JSON.parse(localStorage.getItem(STORAGE_KEYS.downloadsSnapshot) || "[]");
    if (Array.isArray(librarySnapshot)) state.libraryTracks = librarySnapshot;
    if (Array.isArray(downloadsSnapshot)) state.downloadedTracks = downloadsSnapshot;
  } catch (error) {
    state.libraryTracks = [];
    state.downloadedTracks = [];
  }
}

function restorePlayerState() {
  try {
    state.queue = JSON.parse(localStorage.getItem(STORAGE_KEYS.queue) || "[]");
    state.currentTrack = JSON.parse(localStorage.getItem(STORAGE_KEYS.currentTrack) || "null");
    state.shuffle = JSON.parse(localStorage.getItem(STORAGE_KEYS.shuffle) || "false");
    state.repeatMode = localStorage.getItem(STORAGE_KEYS.repeatMode) || "off";
  } catch (error) {
    state.queue = [];
    state.currentTrack = null;
    state.shuffle = false;
    state.repeatMode = "off";
  }
}

function renderLibraryResults(query = "") {
  const container = $("#library-results");
  $("#library-summary").textContent = query
    ? `Найдено в библиотеке: ${state.libraryTracks.length}`
    : `Всего в библиотеке: ${state.libraryTracks.length}`;
  container.innerHTML = "";
  if (!state.libraryTracks.length) {
    container.innerHTML =
      '<div class="lyrics-card empty-state">Библиотека пока пустая. Синхронизируйте лайки или добавьте трек из поиска.</div>';
    return;
  }
  prefetchPlaybackUrls(state.libraryTracks);
  state.libraryTracks.forEach((track) => {
    container.appendChild(createTrackCard(track, { openLyricsButton: true, collectionTracks: state.libraryTracks }));
  });
}

function renderDownloadsResults(query = "") {
  const container = $("#downloads-results");
  $("#downloads-summary").textContent = query
    ? `Найдено среди скачанных: ${state.downloadedTracks.length}`
    : `Скачанных треков: ${state.downloadedTracks.length}`;
  container.innerHTML = "";
  if (!state.downloadedTracks.length) {
    container.innerHTML = '<div class="lyrics-card empty-state">Скачанных треков пока нет.</div>';
    return;
  }
  prefetchPlaybackUrls(state.downloadedTracks);
  state.downloadedTracks.forEach((track) => {
    container.appendChild(createTrackCard(track, { openLyricsButton: true, collectionTracks: state.downloadedTracks }));
  });
}

function shuffleArray(items) {
  const copy = [...items];
  for (let i = copy.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [copy[i], copy[j]] = [copy[j], copy[i]];
  }
  return copy;
}

async function request(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || payload.message || "Запрос не выполнен.");
  }
  return payload;
}

function switchTab(tabName) {
  state.activeTab = tabName;
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.classList.toggle("is-active", tab.dataset.tab === tabName);
  });
  document.querySelectorAll(".panel").forEach((panel) => {
    panel.classList.toggle("is-active", panel.id === `tab-${tabName}`);
  });
}

function renderTrackMeta(track) {
  return [
    track.album || null,
    track.duration_seconds ? formatDuration(track.duration_seconds) : null,
    downloadedLabel(track),
  ]
    .filter(Boolean)
    .join(" • ");
}

async function requestPlaybackUrl(track) {
  ensureAccess();
  const key = trackKey(track);
  if (state.playbackCache[key]) {
    return await state.playbackCache[key];
  }

  const pending = request("/api/playback-url", {
    method: "POST",
    body: JSON.stringify({ ...track, telegram_user_id: ensureUserId() }),
  })
    .then((payload) => payload.stream_url)
    .catch((error) => {
      delete state.playbackCache[key];
      throw error;
    });

  state.playbackCache[key] = pending;
  return await pending;
}

function prefetchPlaybackUrls(tracks) {
  if (!hasAccess()) return;
  tracks.slice(0, 12).forEach((track) => {
    const key = trackKey(track);
    if (state.playbackCache[key]) return;
    state.playbackCache[key] = request("/api/playback-url", {
      method: "POST",
      body: JSON.stringify({ ...track, telegram_user_id: currentUserId() }),
    })
      .then((payload) => payload.stream_url)
      .catch(() => {
        delete state.playbackCache[key];
        return null;
      });
  });
}

async function fetchDownloadPayload(track) {
  ensureAccess();
  return request("/api/download-url", {
    method: "POST",
    body: JSON.stringify({ ...track, telegram_user_id: ensureUserId() }),
  });
}

async function markTrackDownloaded(track, bucket = "library") {
  await request("/api/library/mark-downloaded", {
    method: "POST",
    body: JSON.stringify({
      telegram_user_id: currentUserId(),
      source: track.source,
      source_track_id: track.source_track_id,
      bucket,
    }),
  });
  const timestamp = new Date().toISOString();
  track.download_requested_at = timestamp;
  const mirror = getLibraryMirror(track);
  if (mirror) mirror.download_requested_at = timestamp;
}

async function triggerTrackDownload(track, bucket = "library") {
  const payload = await fetchDownloadPayload(track);
  const anchor = document.createElement("a");
  anchor.href = payload.download_url;
  anchor.download = payload.filename || `${track.title || "track"}.mp3`;
  anchor.target = "_blank";
  anchor.rel = "noreferrer";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  await markTrackDownloaded(track, bucket);
}

function updateMediaSession() {
  if (!("mediaSession" in navigator)) return;
  const audio = $("#audio-player");
  const track = state.currentTrack;
  navigator.mediaSession.playbackState = track && !audio.paused ? "playing" : "paused";
  if (!track) {
    navigator.mediaSession.metadata = null;
    return;
  }
  const artwork = track.cover_url
    ? [
        { src: track.cover_url, sizes: "96x96", type: "image/jpeg" },
        { src: track.cover_url, sizes: "192x192", type: "image/jpeg" },
        { src: track.cover_url, sizes: "512x512", type: "image/jpeg" },
      ]
    : [];
  navigator.mediaSession.metadata = new MediaMetadata({
    title: track.title || "Без названия",
    artist: track.artists || "Неизвестный артист",
    album: track.album || "",
    artwork,
  });
  if (navigator.mediaSession.setPositionState && audio.duration) {
    try {
      navigator.mediaSession.setPositionState({
        duration: audio.duration || 0,
        playbackRate: audio.playbackRate || 1,
        position: audio.currentTime || 0,
      });
    } catch (error) {
      console.warn(error);
    }
  }
}

function buildQueueFromCollection(track, collectionTracks, { wrap = false } = {}) {
  state.currentCollection = Array.isArray(collectionTracks) ? collectionTracks : [];
  const current = trackKey(track);
  const currentIndex = state.currentCollection.findIndex((item) => trackKey(item) === current);
  if (currentIndex === -1) {
    state.queue = [];
    return;
  }

  if (state.shuffle) {
    state.queue = shuffleArray(state.currentCollection.filter((item) => trackKey(item) !== current));
    return;
  }

  const after = state.currentCollection.slice(currentIndex + 1);
  const before = wrap ? state.currentCollection.slice(0, currentIndex) : [];
  state.queue = after.concat(before);
}

function updateModeButtons() {
  if ($("#shuffle-toggle")) {
    $("#shuffle-toggle").textContent = `Перемешивание: ${state.shuffle ? "вкл" : "выкл"}`;
    $("#shuffle-toggle").classList.toggle("is-active", state.shuffle);
  }
  if ($("#repeat-toggle")) {
    const label = state.repeatMode === "one" ? "трек" : state.repeatMode === "all" ? "очередь" : "выкл";
    $("#repeat-toggle").textContent = `Повтор: ${label}`;
    $("#repeat-toggle").classList.toggle("is-active", state.repeatMode !== "off");
  }
}

function renderQueue() {
  const container = $("#queue-list");
  if (!container) return;
  container.innerHTML = "";

  if (!state.queue.length) {
    container.innerHTML =
      '<div class="lyrics-card empty-state">Очередь пуста. Добавьте треки кнопкой "Слушать следующим" или запустите список из поиска, библиотеки или скачанных.</div>';
    persistPlayerState();
    return;
  }

  const template = $("#queue-item-template");
  state.queue.forEach((track, index) => {
    const node = template.content.firstElementChild.cloneNode(true);
    node.querySelector(".queue-item__title").textContent = `${index + 1}. ${track.title}`;
    node.querySelector(".queue-item__artist").textContent = track.artists || "Неизвестный артист";
    node.querySelector(".queue-item__body").addEventListener("click", async () => {
      const [selected] = state.queue.splice(index, 1);
      renderQueue();
      await playTrack(selected);
    });
    node.querySelector(".queue-item__remove").addEventListener("click", () => {
      state.queue.splice(index, 1);
      renderQueue();
      updatePlayerUi();
    });
    container.appendChild(node);
  });

  persistPlayerState();
}

async function playTrack(track) {
  const audio = $("#audio-player");
  const streamUrl = await requestPlaybackUrl(track);
  if (!streamUrl) {
    throw new Error("Не удалось получить поток для этого трека.");
  }
  state.currentTrack = { ...track, stream_url: streamUrl };
  audio.src = streamUrl;
  audio.currentTime = 0;
  await audio.play();
  switchTab("player");
  updatePlayerUi();
}

async function startCollectionPlayback(track, collectionTracks) {
  buildQueueFromCollection(track, collectionTracks, { wrap: state.repeatMode === "all" });
  renderQueue();
  await playTrack(track);
}

function enqueueTrack(track, { next = false } = {}) {
  const key = trackKey(track);
  const currentKey = state.currentTrack ? trackKey(state.currentTrack) : null;
  if (currentKey === key) {
    renderQueue();
    updatePlayerUi();
    return;
  }

  state.queue = state.queue.filter((item) => trackKey(item) !== key);
  if (next) {
    const tail = state.queue.filter((item) => trackKey(item) !== key);
    state.queue = [track, ...tail];
  }
  else state.queue.push(track);
  renderQueue();
  updatePlayerUi();
  setStatusText(next ? "Трек поставлен следующим." : "Трек добавлен в очередь.");
}

async function playNextTrack() {
  const audio = $("#audio-player");

  if (state.repeatMode === "one" && state.currentTrack) {
    audio.currentTime = 0;
    await audio.play();
    updatePlayerUi();
    return;
  }

  if (!state.queue.length && state.currentCollection.length && state.currentTrack) {
    buildQueueFromCollection(state.currentTrack, state.currentCollection, { wrap: state.repeatMode === "all" });
  }

  const nextTrack = state.queue.shift();
  renderQueue();

  if (!nextTrack) {
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

function updatePlayerUi() {
  const audio = $("#audio-player");
  const track = state.currentTrack;
  const hasTrack = Boolean(track);
  const isPlaying = hasTrack && !audio.paused && !audio.ended;

  if ($("#player-title")) $("#player-title").textContent = hasTrack ? track.title : "Ничего не играет";
  if ($("#player-artist")) {
    $("#player-artist").textContent = hasTrack
      ? (track.artists || "Неизвестный артист")
      : "Выберите трек в поиске, библиотеке или скачанных.";
  }
  if ($("#player-current-time")) $("#player-current-time").textContent = formatDuration(audio.currentTime || 0);
  if ($("#player-duration")) $("#player-duration").textContent = formatDuration(hasTrack ? (audio.duration || track.duration_seconds || 0) : 0);
  if ($("#player-seek")) {
    $("#player-seek").value = hasTrack && audio.duration ? Math.round((audio.currentTime / audio.duration) * 100) : 0;
    $("#player-seek").disabled = !hasTrack;
  }

  const cover = $("#player-cover");
  if (cover) {
    if (hasTrack && track.cover_url) {
      cover.style.backgroundImage = `url(${track.cover_url})`;
      cover.style.backgroundSize = "cover";
      cover.style.backgroundPosition = "center";
    } else {
      cover.style.backgroundImage = "";
    }
  }

  if ($("#player-toggle")) $("#player-toggle").textContent = hasTrack ? (isPlaying ? "Пауза" : "Слушать") : "Слушать";
  if ($("#player-prev")) $("#player-prev").disabled = !hasTrack;
  if ($("#player-next")) $("#player-next").disabled = !hasTrack && state.queue.length === 0;

  if ($("#player-dock")) $("#player-dock").classList.toggle("is-hidden", !hasTrack);
  if ($("#player-dock-title")) $("#player-dock-title").textContent = hasTrack ? track.title : "Ничего не играет";
  if ($("#player-dock-subtitle")) $("#player-dock-subtitle").textContent = hasTrack ? (track.artists || "Неизвестный артист") : "Выберите трек";
  if ($("#player-dock-toggle")) $("#player-dock-toggle").textContent = isPlaying ? "⏸" : "▶";

  updateModeButtons();
  updateMediaSession();
  persistPlayerState();
}

function createTrackCard(track, { saveButton = false, openLyricsButton = false, collectionTracks = [] } = {}) {
  const template = $("#track-card-template");
  const node = template.content.firstElementChild.cloneNode(true);
  node.querySelector(".track-card__title").textContent = track.title;
  node.querySelector(".track-card__artist").textContent = track.artists || "Неизвестный артист";
  node.querySelector(".track-card__meta").textContent = renderTrackMeta(track);
  node.querySelector(".track-card__source").textContent = track.source;

  const cover = node.querySelector(".track-card__cover");
  if (track.cover_url) {
    cover.style.backgroundImage = `url(${track.cover_url})`;
    cover.style.backgroundSize = "cover";
    cover.style.backgroundPosition = "center";
  }

  const actions = node.querySelector(".track-card__actions");

  const playButton = document.createElement("button");
  playButton.type = "button";
  playButton.className = "primary-button";
  playButton.textContent = "Слушать";
  playButton.addEventListener("click", async () => {
    try {
      await startCollectionPlayback(track, collectionTracks);
    } catch (error) {
      setStatusText(error.message);
    }
  });
  actions.appendChild(playButton);

  const queueButton = document.createElement("button");
  queueButton.type = "button";
  queueButton.className = "ghost-button";
  queueButton.textContent = "Слушать следующим";
  queueButton.addEventListener("click", () => enqueueTrack(track, { next: true }));
  actions.appendChild(queueButton);

  const downloadButton = document.createElement("button");
  downloadButton.type = "button";
  downloadButton.className = "ghost-button";
  downloadButton.textContent = isTrackDownloaded(track) ? "Скачать заново" : "Скачать";
  downloadButton.addEventListener("click", async () => {
    try {
      await triggerTrackDownload(track, track.bucket || "library");
      downloadButton.textContent = "Скачать заново";
      node.querySelector(".track-card__meta").textContent = renderTrackMeta(track);
      await loadDownloads($("#downloads-query")?.value?.trim() || "");
    } catch (error) {
      setStatusText(error.message);
    }
  });
  actions.appendChild(downloadButton);

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
    const saveButtonNode = document.createElement("button");
    const alreadySaved = Boolean(getLibraryMirror(track));
    saveButtonNode.type = "button";
    saveButtonNode.className = "ghost-button";
    saveButtonNode.textContent = alreadySaved ? "Уже в библиотеке" : "В библиотеку";
    saveButtonNode.addEventListener("click", async () => {
      try {
        ensureAccess();
        const payload = await request("/api/library/tracks", {
          method: "POST",
          body: JSON.stringify({ ...track, bucket: "library", telegram_user_id: ensureUserId() }),
        });
        const responseTracks = Array.isArray(payload.tracks) ? payload.tracks : [];
        if (responseTracks.length) {
          state.libraryTracks = responseTracks;
          persistLibrarySnapshot();
        } else if (!getLibraryMirror(track)) {
          state.libraryTracks.unshift({
            ...track,
            bucket: "library",
            download_requested_at: track.download_requested_at || null,
          });
          persistLibrarySnapshot();
        }
        const queryNode = $("#library-query");
        if (queryNode) queryNode.value = "";
        renderLibraryResults("");
        await loadLibrary("");
        saveButtonNode.textContent = "Уже в библиотеке";
        setStatusText("Трек добавлен в библиотеку.", "library");
      } catch (error) {
        setStatusText(error.message, "library");
      }
    });
    actions.appendChild(saveButtonNode);
  }

  if (openLyricsButton) {
    const lyricsButton = document.createElement("button");
    lyricsButton.type = "button";
    lyricsButton.className = "ghost-button";
    lyricsButton.textContent = "Текст";
    lyricsButton.addEventListener("click", () => {
      switchTab("lyrics");
      $("#lyrics-query").value = `${track.title} ${track.artists || ""}`.trim();
    });
    actions.appendChild(lyricsButton);
  }

  return node;
}

async function loadHealth() {
  const payload = await request("/api/health");
  state.health = payload;
  if ($("#health-pill")) $("#health-pill").textContent = payload.ok ? "Сервисы доступны" : "Есть проблемы";
  if ($("#status-card")) {
    $("#status-card").textContent =
      `Приложение: ${payload.app_name}\n` +
      `Яндекс.Музыка: ${payload.sources.yandex ? "подключена" : "не подключена"}\n` +
      `YouTube: ${payload.sources.youtube ? "доступен" : "недоступен"}\n` +
      `Тексты песен: ${payload.sources.lyrics ? "включены" : "выключены"}\n\n` +
      `Библиотека: ${payload.stats.library_count}\n` +
      `Лайки: ${payload.stats.liked_count}\n` +
      `Пользователи Mini App: ${payload.stats.user_count}`;
  }
}

function renderAccessStatus() {
  const node = $("#promo-status");
  if (!node) return;
  const status = state.accessStatus || { access_type: "free", source: "none", promo_code: null, expires_at: null };
  if (status.access_type === "admin") {
    node.textContent = "Права администратора.";
    return;
  }
  if (status.access_type === "premium") {
    const sourceLabel = status.promo_code ? `Промокод: ${status.promo_code}` : "Доступ активен";
    const expires = status.expires_at ? ` до ${String(status.expires_at).split("T")[0].split(" ")[0]}` : " без срока";
    node.textContent = `${sourceLabel}${expires}`;
    return;
  }
  node.textContent = "Доступ Mini App не активирован.";
}

async function loadAccessStatus() {
  const payload = await request(
    `/api/access/status?telegram_user_id=${encodeURIComponent(currentUserId())}`
  );
  state.accessStatus = payload.status || null;
  renderAccessStatus();
}

async function activatePromo(event) {
  event.preventDefault();
  const input = $("#promo-code-input");
  const code = input.value.trim();
  if (!code) {
    renderAccessStatus();
    $("#promo-status").textContent = "Введите промокод.";
    return;
  }
  const payload = await request("/api/access/promo", {
    method: "POST",
    body: JSON.stringify({ telegram_user_id: ensureUserId(), code }),
  });
  state.accessStatus = payload.status || null;
  input.value = "";
  renderAccessStatus();
}

async function performSearch(event) {
  event.preventDefault();
  ensureAccess();
  const query = $("#search-query").value.trim();
  if (!query) return;
  setStatusText("Ищу треки...");
  const payload = await request(`/api/search?q=${encodeURIComponent(query)}&source=${state.searchSource}&limit=50`);
  setStatusText(`Найдено: ${payload.total}`);
  const container = $("#search-results");
  container.innerHTML = "";
  prefetchPlaybackUrls(payload.results);
  payload.results.forEach((track) => {
    container.appendChild(
      createTrackCard(track, { saveButton: true, openLyricsButton: true, collectionTracks: payload.results })
    );
  });
}

async function performLyricsSearch(event) {
  event.preventDefault();
  ensureAccess();
  const query = $("#lyrics-query").value.trim();
  if (!query) return;
  $("#lyrics-result").textContent = "Ищу текст...";
  const payload = await request(`/api/lyrics?q=${encodeURIComponent(query)}&source=${state.lyricsSource}`);
  if (!payload.found) {
    $("#lyrics-result").textContent = payload.error || "Текст не найден.";
    return;
  }
  const lyrics = payload.lyrics;
  $("#lyrics-result").textContent = `${lyrics.title}\n${lyrics.artists}\nИсточник: ${lyrics.source}\n\n${lyrics.text}`;
}

async function loadLibrary(query = "") {
  ensureAccess();
  const payload = await request(
    `/api/library?bucket=library&limit=2000&telegram_user_id=${encodeURIComponent(currentUserId())}&query=${encodeURIComponent((query || "").trim())}`
  );
  const tracks = Array.isArray(payload.tracks) ? payload.tracks : [];
  if (tracks.length) {
    state.libraryTracks = tracks;
    persistLibrarySnapshot();
  } else if (!query && state.libraryTracks.length) {
    persistLibrarySnapshot();
  } else if (!query) {
    const likedPayload = await request(
      `/api/library?bucket=liked&limit=2000&telegram_user_id=${encodeURIComponent(currentUserId())}&query=`
    );
    const likedTracks = Array.isArray(likedPayload.tracks) ? likedPayload.tracks : [];
    if (likedTracks.length) {
      state.libraryTracks = likedTracks.map((track) => ({ ...track, bucket: "library" }));
      persistLibrarySnapshot();
    } else {
      restoreLibrarySnapshot();
    }
  }
  renderLibraryResults(query);
}

async function loadDownloads(query = "") {
  ensureAccess();
  const payload = await request(
    `/api/library?bucket=library&downloaded_only=1&limit=2000&telegram_user_id=${encodeURIComponent(currentUserId())}&query=${encodeURIComponent((query || "").trim())}`
  );
  const tracks = Array.isArray(payload.tracks) ? payload.tracks : [];
  if (tracks.length) {
    state.downloadedTracks = tracks;
    persistLibrarySnapshot();
  } else if (!query) {
    restoreLibrarySnapshot();
  }
  renderDownloadsResults(query);
}

function renderLibrarySyncStats(result) {
  const stats = $("#library-sync-stats");
  stats.innerHTML = "";
  [
    ["Всего", result.total],
    ["Новых", result.new_count],
    ["Уже было", result.existing_count],
    ["Удалено", result.removed_count],
  ].forEach(([label, value]) => {
    const box = document.createElement("div");
    box.className = "stat-box";
    box.innerHTML = `<span>${label}</span><strong>${value}</strong>`;
    stats.appendChild(box);
  });
}

async function syncLibraryFromLiked() {
  ensureAccess();
  setStatusText("Синхронизация библиотеки запущена...", "library-sync");
  const payload = await request("/api/liked/sync", {
    method: "POST",
    body: JSON.stringify({ telegram_user_id: ensureUserId() }),
  });
  setStatusText(payload.message, "library-sync");
  if (payload.result) renderLibrarySyncStats(payload.result);
  if (Array.isArray(payload.tracks) && payload.tracks.length) {
    state.libraryTracks = payload.tracks.map((track) => ({ ...track, bucket: "library" }));
    persistLibrarySnapshot();
    renderLibraryResults("");
  } else {
    const likedPayload = await request(
      `/api/library?bucket=liked&limit=2000&telegram_user_id=${encodeURIComponent(ensureUserId())}&query=`
    );
    const likedTracks = Array.isArray(likedPayload.tracks) ? likedPayload.tracks : [];
    if (likedTracks.length) {
      state.libraryTracks = likedTracks.map((track) => ({ ...track, bucket: "library" }));
      persistLibrarySnapshot();
      renderLibraryResults("");
    }
  }
  const queryNode = $("#library-query");
  if (queryNode) queryNode.value = "";
  await loadLibrary($("#library-query")?.value?.trim() || "");
  await loadDownloads($("#downloads-query")?.value?.trim() || "");
  await loadHealth();
}

async function downloadLibraryTracks() {
  ensureAccess();
  const pendingTracks = state.libraryTracks.filter((track) => !isTrackDownloaded(track));
  if (!pendingTracks.length) {
    setStatusText("Все треки в библиотеке уже отмечены как скачанные.", "library-sync");
    return;
  }

  setStatusText(`Запускаю скачивание: ${pendingTracks.length} треков.`, "library-sync");
  for (const [index, track] of pendingTracks.entries()) {
    await triggerTrackDownload(track, "library");
    setStatusText(`Скачано ${index + 1} из ${pendingTracks.length}.`, "library-sync");
    await new Promise((resolve) => setTimeout(resolve, 120));
  }
  await loadLibrary($("#library-query")?.value?.trim() || "");
  await loadDownloads($("#downloads-query")?.value?.trim() || "");
}

async function importPlaylistByUrl(event) {
  event.preventDefault();
  ensureAccess();
  const url = $("#playlist-url").value.trim();
  if (!url) {
    setStatusText("Вставьте ссылку на плейлист Яндекс.Музыки.", "library-sync");
    return;
  }

  setStatusText("Добавляю плейлист в библиотеку...", "library-sync");
  const payload = await request("/api/yandex/playlist/import", {
    method: "POST",
    body: JSON.stringify({ telegram_user_id: ensureUserId(), url }),
  });
  $("#playlist-url").value = "";
  setStatusText(`${payload.message} Импортировано новых: ${payload.imported}, уже было: ${payload.existing}.`, "library-sync");
  if (Array.isArray(payload.tracks) && payload.tracks.length) {
    state.libraryTracks = payload.tracks;
    persistLibrarySnapshot();
    renderLibraryResults($("#library-query")?.value?.trim() || "");
  }
  await loadLibrary($("#library-query")?.value?.trim() || "");
  await loadHealth();
}

async function bootstrap() {
  const tg = window.Telegram?.WebApp;
  if (tg) {
    tg.ready();
    tg.expand();
    tg.enableClosingConfirmation?.();
    state.telegramUser = tg.initDataUnsafe?.user || null;
    if (state.telegramUser?.id) {
      state.telegramUserId = Number(state.telegramUser.id) || 0;
      localStorage.setItem(STORAGE_KEYS.userId, String(state.telegramUserId));
    }
    try {
      const sessionPayload = await request("/api/session", {
        method: "POST",
        body: JSON.stringify({
          init_data: tg.initData || null,
          user: state.telegramUser,
        }),
      });
      const resolvedUserId = Number(sessionPayload.telegram_user_id || sessionPayload.user?.id || 0);
      if (resolvedUserId > 0) {
        state.telegramUserId = resolvedUserId;
        localStorage.setItem(STORAGE_KEYS.userId, String(resolvedUserId));
      }
      if (!state.telegramUser && sessionPayload.user) {
        state.telegramUser = sessionPayload.user;
      }
    } catch (error) {
      console.warn(error);
    }
    if ($("#user-greeting")) {
      $("#user-greeting").textContent = `??????, ${(state.telegramUser && state.telegramUser.first_name) || "????"}`;
    }
  }


  restorePlayerState();
  restoreLibrarySnapshot();
  await loadHealth();
  await loadAccessStatus();
  if (hasAccess()) {
    await loadLibrary("");
    await loadDownloads("");
  } else {
    switchTab("status");
    setStatusText("Доступ к Mini App закрыт. Активируйте промокод или оформите подписку.", "library");
    setStatusText("Сначала активируйте доступ, потом можно синхронизировать библиотеку.", "library-sync");
  }
  renderQueue();
  updatePlayerUi();
}

document.addEventListener("DOMContentLoaded", () => {
  try {
    document.querySelectorAll(".tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        switchTab(tab.dataset.tab);
        if (tab.dataset.tab === "library") {
          loadLibrary($("#library-query")?.value?.trim() || "").catch((error) => setStatusText(error.message, "library"));
        }
        if (tab.dataset.tab === "downloads") {
          loadDownloads($("#downloads-query")?.value?.trim() || "").catch((error) => setStatusText(error.message, "downloads"));
        }
      });
    });

    document.querySelectorAll("#search-source .segmented__item").forEach((button) => {
      button.addEventListener("click", () => setActiveSegment("search-source", button.dataset.value, "searchSource"));
    });
    document.querySelectorAll("#lyrics-source .segmented__item").forEach((button) => {
      button.addEventListener("click", () => setActiveSegment("lyrics-source", button.dataset.value, "lyricsSource"));
    });

    $("#search-form").addEventListener("submit", (event) => performSearch(event).catch((error) => setStatusText(error.message)));
    $("#lyrics-form").addEventListener("submit", (event) => {
      performLyricsSearch(event).catch((error) => {
        $("#lyrics-result").textContent = error.message;
      });
    });
    $("#library-search-form").addEventListener("submit", (event) => {
      event.preventDefault();
      loadLibrary($("#library-query").value.trim()).catch((error) => setStatusText(error.message, "library"));
    });
    $("#downloads-search-form").addEventListener("submit", (event) => {
      event.preventDefault();
      loadDownloads($("#downloads-query").value.trim()).catch((error) => setStatusText(error.message, "downloads"));
    });
    $("#library-sync-button").addEventListener("click", () => syncLibraryFromLiked().catch((error) => setStatusText(error.message, "library-sync")));
    $("#library-download-all-button").addEventListener("click", () => downloadLibraryTracks().catch((error) => setStatusText(error.message, "library-sync")));
    $("#playlist-import-form").addEventListener("submit", (event) => importPlaylistByUrl(event).catch((error) => setStatusText(error.message, "library-sync")));
    $("#promo-form").addEventListener("submit", (event) => activatePromo(event).catch((error) => {
      $("#promo-status").textContent = error.message;
    }));

    $("#shuffle-toggle").addEventListener("click", () => {
      state.shuffle = !state.shuffle;
      if (state.currentTrack && state.currentCollection.length) {
        buildQueueFromCollection(state.currentTrack, state.currentCollection, { wrap: state.repeatMode === "all" });
        renderQueue();
      }
      updatePlayerUi();
    });
    $("#repeat-toggle").addEventListener("click", () => {
      state.repeatMode = state.repeatMode === "off" ? "all" : state.repeatMode === "all" ? "one" : "off";
      updatePlayerUi();
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
      if (audio.paused) await audio.play();
      else audio.pause();
      updatePlayerUi();
    });
    $("#player-prev").addEventListener("click", playPreviousTrack);
    $("#player-next").addEventListener("click", () => playNextTrack().catch((error) => setStatusText(error.message)));
    $("#queue-clear").addEventListener("click", () => {
      state.queue = [];
      renderQueue();
      updatePlayerUi();
    });
    $("#player-dock-open").addEventListener("click", () => switchTab("player"));
    $("#player-dock-toggle").addEventListener("click", () => $("#player-toggle").click());
    $("#player-dock-next").addEventListener("click", () => $("#player-next").click());
    $("#player-seek").addEventListener("input", (event) => {
      const audio = $("#audio-player");
      if (!audio.duration) return;
      audio.currentTime = audio.duration * (Number(event.target.value || 0) / 100);
      updatePlayerUi();
    });

    $("#audio-player").addEventListener("timeupdate", updatePlayerUi);
    $("#audio-player").addEventListener("loadedmetadata", updatePlayerUi);
    $("#audio-player").addEventListener("play", updatePlayerUi);
    $("#audio-player").addEventListener("pause", updatePlayerUi);
    $("#audio-player").addEventListener("ended", () => {
      playNextTrack().catch((error) => setStatusText(error.message));
    });
    if ("mediaSession" in navigator) {
      navigator.mediaSession.setActionHandler("play", async () => {
        const audio = $("#audio-player");
        if (state.currentTrack) {
          await audio.play();
          updatePlayerUi();
        }
      });
      navigator.mediaSession.setActionHandler("pause", () => {
        const audio = $("#audio-player");
        audio.pause();
        updatePlayerUi();
      });
      navigator.mediaSession.setActionHandler("nexttrack", () => {
        playNextTrack().catch((error) => setStatusText(error.message));
      });
      navigator.mediaSession.setActionHandler("previoustrack", () => {
        playPreviousTrack();
      });
      navigator.mediaSession.setActionHandler("seekto", (details) => {
        const audio = $("#audio-player");
        if (typeof details.seekTime === "number") {
          audio.currentTime = details.seekTime;
          updatePlayerUi();
        }
      });
      navigator.mediaSession.setActionHandler("seekbackward", () => {
        const audio = $("#audio-player");
        audio.currentTime = Math.max(0, (audio.currentTime || 0) - 10);
        updatePlayerUi();
      });
      navigator.mediaSession.setActionHandler("seekforward", () => {
        const audio = $("#audio-player");
        const duration = Number.isFinite(audio.duration) ? audio.duration : (state.currentTrack?.duration_seconds || 0);
        audio.currentTime = Math.min(duration || 0, (audio.currentTime || 0) + 10);
        updatePlayerUi();
      });
    }

    bootstrap().catch((error) => showFatalError(error.message));
  } catch (error) {
    showFatalError(error.message || "Не удалось запустить интерфейс.");
  }
});

window.addEventListener("error", (event) => {
  showFatalError(event.message || "Ошибка JavaScript.");
});

window.addEventListener("unhandledrejection", (event) => {
  const reason = event.reason?.message || event.reason || "Необработанная ошибка.";
  showFatalError(String(reason));
});
