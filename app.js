let currentStream = null;
let registrationStream = null;
let debugStream = null;
let studentAttendanceStream = null;
let recognitionInstance = null;
let voiceEnabled = true;
let analysisInProgress = false;
const assistantCharts = new WeakMap();
const dashboardCharts = new WeakMap();
const schedulePanelState = new WeakMap();
let countdownTimerHandle = null;
let attendanceTrackingState = null;
let attendanceTrackingHeartbeatHandle = null;
let attendanceTrackingCountdownHandle = null;
let attendanceTrackingStatusPollHandle = null;
let attendanceTrackingWatcherId = null;
let attendanceTrackingLatestPosition = null;
let attendanceTrackingFocusSessionId = "";
let studentAttendanceAnalysisState = null;
let studentAttendanceCameraStartPromise = null;
let studentAttendanceAnalysisInFlight = false;
let studentAttendanceMarkInFlight = false;
let attendanceTrackingHeartbeatInFlight = false;
let attendanceTrackingStatusRestoreInFlight = false;
let studentAttendanceAnalysisRequestId = 0;
let attendanceTrackingRestoreRequestId = 0;
let attendanceTrackingHeartbeatRequestId = 0;
const ANALYSIS_FRAME_BURST = 4;
const ANALYSIS_CAPTURE_WIDTH = 480;
const ANALYSIS_CAPTURE_HEIGHT = 360;
const THEME_STORAGE_KEY = "attendance-theme";
const TRACKING_HEARTBEAT_INTERVAL_MS = 15000;
const TRACKING_STATUS_POLL_INTERVAL_MS = 5000;
const GPS_READING_MAX_AGE_MS = 20000;
const GPS_HIGH_ACCURACY_OPTIONS = {
    enableHighAccuracy: true,
    timeout: 15000,
    maximumAge: 0,
};

function enableEnhancedMotion() {
    if (document.body) {
        document.body.classList.add("js-enhanced");
    }
}

function setProcessingState(element, isProcessing) {
    if (!element) return;
    element.classList.toggle("is-processing", Boolean(isProcessing));
}

function resetStudentAttendanceAnalysisState() {
    studentAttendanceAnalysisState = {
        ready: false,
        sessionId: "",
        position: null,
    };
}

function normalizeSessionId(value) {
    return String(value ?? "").trim();
}

function formatMetersValue(value) {
    const numericValue = Number(value);
    if (!Number.isFinite(numericValue)) return "N/A";
    return `${numericValue.toFixed(numericValue >= 100 ? 1 : 2)} m`;
}

function isFreshGeolocationPosition(position, maxAgeMs = GPS_READING_MAX_AGE_MS) {
    const timestamp = Number(position?.timestamp || 0);
    return Boolean(timestamp && (Date.now() - timestamp) <= maxAgeMs);
}

function getCurrentBrowserPosition(options = GPS_HIGH_ACCURACY_OPTIONS) {
    if (!navigator.geolocation || typeof navigator.geolocation.getCurrentPosition !== "function") {
        return Promise.reject(new Error("Geolocation is not supported in this browser."));
    }

    return new Promise((resolve, reject) => {
        navigator.geolocation.getCurrentPosition(resolve, reject, options);
    });
}

function getGeolocationErrorMessage(error) {
    const errorCode = Number(error?.code);
    if (errorCode === 1) return "Location permission denied";
    if (errorCode === 2) return "Unable to fetch location";
    if (errorCode === 3) return "Location request timed out";
    return error?.message || "Unable to fetch location";
}

function createAttendanceGeolocationError(message, gpsState, code = null) {
    const error = new Error(message);
    error.gpsState = gpsState;
    error.code = code;
    error.permissionDenied = gpsState === "GPS_PERMISSION_DENIED";
    return error;
}

function mapAttendanceGeolocationError(error, purpose = "tracking") {
    const errorCode = Number(error?.code);
    if (errorCode === 1) {
        return createAttendanceGeolocationError(
            purpose === "tracking"
                ? "Location permission is denied. Please enable it to continue GPS verification."
                : "Location permission is denied. Please enable it to continue attendance.",
            "GPS_PERMISSION_DENIED",
            errorCode,
        );
    }
    if (errorCode === 2 || errorCode === 3) {
        return createAttendanceGeolocationError(
            "Trying to get a stable GPS reading...",
            "GPS_TEMP_UNAVAILABLE",
            errorCode,
        );
    }
    return createAttendanceGeolocationError(
        "GPS signal is weak. Tracking will continue and valid readings will be used.",
        "GPS_LOW_SIGNAL",
        errorCode || null,
    );
}

function buildStudentAttendanceGpsPayload(position) {
    const latitude = Number(position?.coords?.latitude);
    const longitude = Number(position?.coords?.longitude);
    const accuracyMeters = Number(position?.coords?.accuracy);
    return {
        latitude: Number.isFinite(latitude) ? latitude : null,
        longitude: Number.isFinite(longitude) ? longitude : null,
        accuracy_meters: Number.isFinite(accuracyMeters) ? accuracyMeters : null,
        position_timestamp_ms: Number(position?.timestamp || Date.now()),
    };
}

function formatStudentGpsStatus(data = {}, position = null, tracking = null) {
    const trackingData = tracking && typeof tracking === "object" ? tracking : (attendanceTrackingState || {});
    const gpsState = String(data.gps_state ?? trackingData.gps_state ?? "").trim().toUpperCase();
    const gpsStatusText = String(data.gps_status_text ?? trackingData.gps_status_text ?? "").trim();
    const liveCoords = position?.coords || null;
    const latitude = liveCoords?.latitude ?? data.student_lat ?? trackingData.last_location_latitude;
    const longitude = liveCoords?.longitude ?? data.student_lng ?? trackingData.last_location_longitude;
    const rangeState = String(data.range_state ?? trackingData.range_state ?? "").trim().toLowerCase();
    const reason = String(data.reason || "").trim().toLowerCase();
    const trackingState = String(trackingData.tracking_state || "").trim();
    const hasCoordinates = Number.isFinite(Number(latitude)) && Number.isFinite(Number(longitude));

    if (gpsStatusText) return gpsStatusText;
    if (!hasCoordinates && reason === "ready_to_mark") return "Checked when attendance is marked";
    if (gpsState === "GPS_PERMISSION_DENIED") return "Location permission denied";
    if (gpsState === "GPS_TEMP_UNAVAILABLE") return "Trying to get a stable GPS reading";
    if (gpsState === "GPS_LOW_SIGNAL") return "GPS signal is weak, tracking continues";
    if (!hasCoordinates) return "Not captured yet";
    if (rangeState === "uncertain") return "Low accuracy. Please retry.";
    if (rangeState === "out_of_range") return "Outside allowed area";
    if (trackingState === "Tracking Active") return "Tracking live";
    if (rangeState === "in_range") return "Within allowed area";
    return "Location captured";
}

function syncStudentAttendanceMarkButton(button, data = {}) {
    if (!button) return;

    const sessionId = String(data?.session_id || "");
    const workflowStatus = String(data?.status || data?.tracking?.attendance_status || "").trim().toUpperCase();
    button.dataset.sessionId = sessionId;
    button.dataset.analysisReady = data?.analysis_ready ? "true" : "false";
    button.dataset.attendanceOpen = data?.attendance_open ? "true" : "false";
    button.dataset.sessionLocked = sessionId ? "true" : "false";

    if (data?.already_marked) {
        button.dataset.attendanceState = "marked";
        button.textContent = ["MARKED_PENDING_TRACKING", "TRACKING_ACTIVE"].includes(workflowStatus)
            ? "Temporarily Marked"
            : workflowStatus === "CANCELLED"
                ? "Attendance Cancelled"
                : "Attendance Marked";
        button.classList.add("is-closed-state");
        return;
    }

    if (data?.attendance_open) {
        button.dataset.attendanceState = "open";
        button.textContent = button.dataset.defaultLabel || "Mark Attendance";
        button.classList.remove("is-closed-state");
        return;
    }

    button.dataset.attendanceState = "closed";
    button.textContent = button.dataset.closedLabel || "Attendance Is Closed Now";
    button.classList.add("is-closed-state");
}

function setAttendanceTrackingFocusSessionId(sessionId) {
    attendanceTrackingFocusSessionId = normalizeSessionId(sessionId);
    const trackingCard = document.getElementById("studentTrackingStatusCard");
    if (trackingCard) {
        trackingCard.dataset.focusSessionId = attendanceTrackingFocusSessionId;
    }
}

function isAttendanceTrackingLocked(tracking) {
    const status = String(tracking?.attendance_status || tracking?.status || "").trim().toUpperCase();
    return Boolean(tracking?.available && (status === "MARKED_PENDING_TRACKING" || status === "TRACKING_ACTIVE"));
}

function stopMediaStream(stream) {
    if (!stream) return;
    stream.getTracks().forEach((track) => {
        try {
            track.onended = null;
            if (track.readyState !== "ended") {
                track.stop();
            }
        } catch (error) {
            console.error(error);
        }
    });
}

async function attachVideoStream(video, stream) {
    if (!video) return;
    video.playsInline = true;
    video.muted = true;
    video.srcObject = stream;

    try {
        await video.play();
    } catch (error) {
        console.error(error);
    }
}

function setCameraWrapperState(video, isActive) {
    const wrapper = video?.closest(".video-wrapper");
    if (!wrapper) return;
    wrapper.classList.toggle("camera-active", Boolean(isActive));
}

function watchStream(stream, onEnded) {
    if (!stream) return;
    let handled = false;
    stream.getTracks().forEach((track) => {
        track.onended = () => {
            if (handled) return;
            handled = true;
            if (typeof onEnded === "function") {
                onEnded();
            }
        };
    });
}

function syncCanvasToVideo(canvas, video, fallbackWidth = 480, fallbackHeight = 360) {
    if (!canvas || !video) return;

    const width = video.videoWidth || fallbackWidth;
    const height = video.videoHeight || fallbackHeight;

    if (canvas.width !== width) {
        canvas.width = width;
    }

    if (canvas.height !== height) {
        canvas.height = height;
    }
}

function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}

function renderRecognizedPeople(people, attendanceMarkedNames = [], alreadyMarkedNames = []) {
    const list = document.getElementById("recognizedPeopleList");
    const recognizedCount = document.getElementById("recognizedStudentsCount");
    if (!list) return;

    if (recognizedCount) recognizedCount.textContent = String(Array.isArray(people) ? people.length : 0);

    if (!people || !people.length) {
        list.innerHTML = "<p class='meta-line mb-0'>No registered student matched in this scan.</p>";
        return;
    }

    const markedSet = new Set(attendanceMarkedNames || []);
    const alreadySet = new Set(alreadyMarkedNames || []);

    list.innerHTML = people
        .map((person) => {
            let statusText = "Matched";
            let statusClass = "recognized-status-matched";

            if (markedSet.has(person.name)) {
                statusText = "Attendance Credited";
                statusClass = "recognized-status-marked";
            } else if (alreadySet.has(person.name)) {
                statusText = "Already Present";
                statusClass = "recognized-status-existing";
            }

            return `
                <div class="recognized-person-card">
                    <div>
                        <div class="recognized-person-name">${escapeHtml(person.name || "Unknown")}</div>
                        <div class="recognized-person-meta">Emotion: ${escapeHtml(person.emotion || "Unknown")}</div>
                    </div>
                    <div class="recognized-person-status ${statusClass}">${escapeHtml(statusText)}</div>
                </div>
            `;
        })
        .join("");
}

function getSpeechConfig() {
    return document.getElementById("speechConfig");
}

function speakText(text) {
    if (!voiceEnabled || !("speechSynthesis" in window) || !text) return;

    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(String(text));
    utterance.rate = 1;
    utterance.pitch = 1;
    window.speechSynthesis.speak(utterance);
}

function announceFlashMessages() {
    const flashMessages = document.querySelectorAll(".flash-message");
    const speechConfig = getSpeechConfig();
    if (!flashMessages.length || !speechConfig) return;

    flashMessages.forEach((messageElement) => {
        const text = (messageElement.querySelector(".toast-body")?.textContent || messageElement.textContent || "").trim();
        if (!text) return;

        let spokenText = text;
        if (text.includes("Admin login successful")) {
            spokenText = speechConfig.dataset.welcomeAdmin || "Welcome admin";
        } else if (text.includes("Student registered successfully")) {
            spokenText = speechConfig.dataset.studentRegistered || "Student registered successfully";
        }
        speakText(spokenText);
    });
}

function initializeToasts() {
    if (!window.bootstrap?.Toast) return;
    document.querySelectorAll(".notification-toast").forEach((toastElement) => {
        window.bootstrap.Toast.getOrCreateInstance(toastElement).show();
    });
}

function initializeSidebar() {
    const shell = document.querySelector(".app-shell");
    const toggleBtn = document.getElementById("sidebarToggleBtn");
    const closeBtn = document.getElementById("sidebarCloseBtn");
    const backdrop = document.getElementById("sidebarBackdrop");
    if (!shell) return;

    const closeSidebar = () => shell.classList.remove("sidebar-open");
    const openSidebar = () => shell.classList.add("sidebar-open");

    if (toggleBtn) toggleBtn.addEventListener("click", openSidebar);
    if (closeBtn) closeBtn.addEventListener("click", closeSidebar);
    if (backdrop) backdrop.addEventListener("click", closeSidebar);
}

function updatePillStatus(elementId, label, value, state) {
    const element = document.getElementById(elementId);
    if (!element) return;

    element.textContent = `${label}: ${value}`;
    element.classList.remove("engine-pill-ready", "engine-pill-warning");

    if (state === "ready") {
        element.classList.add("engine-pill-ready");
    } else if (state === "warning") {
        element.classList.add("engine-pill-warning");
    }
}

function setEngineErrors(recognitionError, emotionError) {
    const recognitionElement = document.getElementById("recognitionError");
    const emotionElement = document.getElementById("emotionError");

    if (recognitionElement) {
        recognitionElement.textContent = recognitionError ? `Recognition issue: ${recognitionError}` : "";
    }
    if (emotionElement) {
        emotionElement.textContent = emotionError ? `Emotion issue: ${emotionError}` : "";
    }
}

function updateAttendanceCharts(items) {
    const charts = document.querySelectorAll(".attendance-chart");
    charts.forEach((chart) => {
        if (!items || !items.length) {
            chart.innerHTML = "<p class='meta-line mb-0'>No attendance graph data available yet.</p>";
            return;
        }

        chart.dataset.chart = JSON.stringify(items);
        chart.innerHTML = items
            .map((item) => {
                const totalClasses = Number(item.total_classes || 0);
                const attendance = Number(item.attendance || 0);
                const percentage = totalClasses ? Math.round((attendance / totalClasses) * 100) : 0;
                return `
                    <div class="chart-row">
                        <div class="chart-meta">
                            <span>${escapeHtml(item.name)}</span>
                            <span>${attendance}/${totalClasses} (${percentage}%)</span>
                        </div>
                        <div class="chart-track">
                            <div class="chart-bar" style="width: ${percentage}%"></div>
                        </div>
                    </div>
                `;
            })
            .join("");
    });
}

function markChartShellLoaded(canvas) {
    const shell = canvas?.closest(".chart-loading-shell");
    if (!shell) return;
    window.setTimeout(() => shell.classList.add("is-loaded"), 120);
}

function animateCounterValue(element) {
    if (!element || element.dataset.counterAnimated === "true") return;
    const rawValue = element.dataset.counter ?? element.textContent ?? "0";
    const target = Number.parseFloat(rawValue);
    if (!Number.isFinite(target)) return;

    const duration = 900;
    const start = performance.now();
    const decimals = Number.isInteger(target) ? 0 : 1;

    const tick = (timestamp) => {
        const progress = Math.min((timestamp - start) / duration, 1);
        const eased = 1 - Math.pow(1 - progress, 3);
        const current = target * eased;
        element.textContent = decimals ? current.toFixed(decimals) : String(Math.round(current));
        if (progress < 1) {
            window.requestAnimationFrame(tick);
        } else {
            element.textContent = decimals ? target.toFixed(decimals) : String(target);
            element.dataset.counterAnimated = "true";
        }
    };

    element.textContent = "0";
    window.requestAnimationFrame(tick);
}

function initializeAnimatedCounters() {
    const counters = document.querySelectorAll("[data-counter]");
    if (!counters.length) return;

    if (!("IntersectionObserver" in window)) {
        counters.forEach((counter) => animateCounterValue(counter));
        return;
    }

    const observer = new IntersectionObserver((entries, currentObserver) => {
        entries.forEach((entry) => {
            if (!entry.isIntersecting) return;
            animateCounterValue(entry.target);
            currentObserver.unobserve(entry.target);
        });
    }, { threshold: 0.35 });

    counters.forEach((counter) => observer.observe(counter));
}

function initializeRevealSections() {
    const sections = document.querySelectorAll(".reveal-section");
    if (!sections.length) return;

    if (!("IntersectionObserver" in window)) {
        sections.forEach((section) => section.classList.add("is-visible"));
        return;
    }

    const observer = new IntersectionObserver((entries, currentObserver) => {
        entries.forEach((entry) => {
            if (!entry.isIntersecting) return;
            entry.target.classList.add("is-visible");
            currentObserver.unobserve(entry.target);
        });
    }, { threshold: 0.14, rootMargin: "0px 0px -40px 0px" });

    sections.forEach((section) => observer.observe(section));
}

function triggerAttendanceSuccess() {
    const burst = document.getElementById("attendanceSuccessBurst");
    if (!burst) return;
    burst.classList.remove("is-active");
    void burst.offsetWidth;
    burst.classList.add("is-active");
    window.setTimeout(() => burst.classList.remove("is-active"), 900);
}

function initializeAccessPortalParallax(scene) {
    const root = scene || document.querySelector("[data-ai-scene]");
    if (!root || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    const layers = root.querySelectorAll("[data-parallax-depth]");
    if (!layers.length) return;

    let pointerX = 0;
    let pointerY = 0;
    let currentX = 0;
    let currentY = 0;
    let frameId = null;

    const animate = () => {
        currentX += (pointerX - currentX) * 0.08;
        currentY += (pointerY - currentY) * 0.08;

        layers.forEach((layer) => {
            const depth = Number(layer.dataset.parallaxDepth || 0);
            const translateX = currentX * (depth / 100);
            const translateY = currentY * (depth / 100);
            layer.style.setProperty("--parallax-x", `${translateX}px`);
            layer.style.setProperty("--parallax-y", `${translateY}px`);
        });

        frameId = window.requestAnimationFrame(animate);
    };

    const updatePointer = (clientX, clientY) => {
        const rect = root.getBoundingClientRect();
        pointerX = ((clientX - rect.left) / rect.width - 0.5) * 2;
        pointerY = ((clientY - rect.top) / rect.height - 0.5) * 2;
    };

    window.addEventListener("mousemove", (event) => updatePointer(event.clientX, event.clientY), { passive: true });
    window.addEventListener("mouseleave", () => {
        pointerX = 0;
        pointerY = 0;
    });

    window.addEventListener("touchmove", (event) => {
        const touch = event.touches?.[0];
        if (!touch) return;
        updatePointer(touch.clientX, touch.clientY);
    }, { passive: true });

    window.addEventListener("touchend", () => {
        pointerX = 0;
        pointerY = 0;
    }, { passive: true });

    if (!frameId) {
        frameId = window.requestAnimationFrame(animate);
    }
}

function initializeAccessPortalBackground() {
    const scene = document.querySelector("[data-ai-scene]");
    const canvas = scene?.querySelector("[data-access-bg-canvas]");
    if (!scene || !canvas) return;

    initializeAccessPortalParallax(scene);

    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    const context = canvas.getContext("2d");
    if (!context) return;

    const particles = [];
    const deviceScale = Math.min(window.devicePixelRatio || 1, 1.5);
    let width = 0;
    let height = 0;
    let pointerX = 0;
    let pointerY = 0;
    let rafId = null;

    const getClusterAnchor = () => ({
        x: width * 0.66,
        y: height * 0.34,
    });

    const createParticle = () => {
        const clusterAnchor = getClusterAnchor();
        const clustered = Math.random() < 0.72;
        const orbitRadius = clustered ? (24 + Math.random() * Math.min(width, height) * 0.17) : (50 + Math.random() * Math.min(width, height) * 0.42);
        const orbitAngle = Math.random() * Math.PI * 2;
        const orbitSpeed = (clustered ? 0.0012 : 0.00045) + Math.random() * (clustered ? 0.0016 : 0.0006);
        const z = (clustered ? 0.65 : 0.3) + Math.random() * (clustered ? 0.95 : 0.7);

        return {
            clustered,
            anchorBias: Math.random(),
            x: clustered ? clusterAnchor.x + Math.cos(orbitAngle) * orbitRadius : Math.random() * width,
            y: clustered ? clusterAnchor.y + Math.sin(orbitAngle) * orbitRadius * 0.72 : Math.random() * height,
            z,
            radius: clustered ? (1 + Math.random() * 2.8) : (0.4 + Math.random() * 1.8),
            vx: (-0.12 + Math.random() * 0.24) * 0.12,
            vy: (-0.12 + Math.random() * 0.24) * 0.12,
            drift: Math.random() * Math.PI * 2,
            orbitRadius,
            orbitAngle,
            orbitSpeed,
            twinkleOffset: Math.random() * Math.PI * 2,
        };
    };

    const resize = () => {
        const rect = scene.getBoundingClientRect();
        width = Math.max(1, Math.floor(rect.width));
        height = Math.max(1, Math.floor(rect.height));
        canvas.width = Math.floor(width * deviceScale);
        canvas.height = Math.floor(height * deviceScale);
        canvas.style.width = `${width}px`;
        canvas.style.height = `${height}px`;
        context.setTransform(deviceScale, 0, 0, deviceScale, 0, 0);

        const count = width < 768 ? 54 : 96;
        while (particles.length < count) {
            particles.push(createParticle());
        }
        particles.length = count;
    };

    const draw = (time) => {
        context.clearRect(0, 0, width, height);

        const clusterAnchor = getClusterAnchor();
        const clusterX = clusterAnchor.x + pointerX * 22;
        const clusterY = clusterAnchor.y + pointerY * 16;
        const secondaryX = width * 0.57 - pointerX * 12;
        const secondaryY = height * 0.54 - pointerY * 10;

        const nebula = context.createRadialGradient(clusterX, clusterY, 8, clusterX, clusterY, Math.max(width, height) * 0.24);
        nebula.addColorStop(0, "rgba(103, 232, 249, 0.15)");
        nebula.addColorStop(0.35, "rgba(59, 130, 246, 0.09)");
        nebula.addColorStop(1, "rgba(34, 211, 238, 0)");
        context.fillStyle = nebula;
        context.fillRect(0, 0, width, height);

        const nebulaTwo = context.createRadialGradient(secondaryX, secondaryY, 8, secondaryX, secondaryY, Math.max(width, height) * 0.2);
        nebulaTwo.addColorStop(0, "rgba(96, 165, 250, 0.11)");
        nebulaTwo.addColorStop(0.45, "rgba(99, 102, 241, 0.06)");
        nebulaTwo.addColorStop(1, "rgba(96, 165, 250, 0)");
        context.fillStyle = nebulaTwo;
        context.fillRect(0, 0, width, height);

        for (let index = 0; index < particles.length; index += 1) {
            const particle = particles[index];
            particle.drift += 0.005 + particle.z * 0.003;
            if (particle.clustered) {
                particle.orbitAngle += particle.orbitSpeed;
                const anchorInfluenceX = clusterX + Math.cos(time * 0.00016 + particle.anchorBias * 4) * 16 * particle.anchorBias;
                const anchorInfluenceY = clusterY + Math.sin(time * 0.00018 + particle.anchorBias * 5) * 14 * particle.anchorBias;
                particle.x = anchorInfluenceX
                    + Math.cos(particle.orbitAngle + particle.drift * 0.28) * particle.orbitRadius
                    + Math.cos(particle.drift * 1.4) * 7 * particle.z
                    + pointerX * 5.5 * particle.z;
                particle.y = anchorInfluenceY
                    + Math.sin(particle.orbitAngle * 1.08 + particle.drift * 0.22) * particle.orbitRadius * 0.62
                    + Math.sin(particle.drift * 1.1) * 6 * particle.z
                    + pointerY * 4.8 * particle.z;
            } else {
                particle.x += particle.vx + Math.cos(particle.drift) * 0.12 * particle.z + pointerX * 0.0035 * particle.z;
                particle.y += particle.vy + Math.sin(particle.drift * 0.9) * 0.12 * particle.z + pointerY * 0.0035 * particle.z;

                if (particle.x < -30) particle.x = width + 30;
                if (particle.x > width + 30) particle.x = -30;
                if (particle.y < -30) particle.y = height + 30;
                if (particle.y > height + 30) particle.y = -30;
            }
        }

        for (let index = 0; index < particles.length; index += 1) {
            const particle = particles[index];
            for (let next = index + 1; next < particles.length; next += 1) {
                const other = particles[next];
                const dx = particle.x - other.x;
                const dy = particle.y - other.y;
                const distance = Math.sqrt(dx * dx + dy * dy);
                const threshold = particle.clustered && other.clustered
                    ? 88 * ((particle.z + other.z) / 2)
                    : 54 * ((particle.z + other.z) / 2);
                if (distance > threshold) continue;

                const alpha = (1 - distance / threshold) * (particle.clustered && other.clustered ? 0.19 : 0.06);
                context.strokeStyle = `rgba(125, 211, 252, ${alpha.toFixed(3)})`;
                context.lineWidth = particle.clustered && other.clustered ? 0.85 : 0.45;
                context.beginPath();
                context.moveTo(particle.x, particle.y);
                context.lineTo(other.x, other.y);
                context.stroke();
            }
        }

        particles.forEach((particle) => {
            const pulse = 0.55 + Math.sin(time * 0.001 + particle.drift * 2.4 + particle.twinkleOffset) * 0.18;
            const alpha = particle.clustered
                ? (0.24 + particle.z * 0.24 + pulse * 0.08)
                : (0.1 + particle.z * 0.16 + pulse * 0.03);
            context.beginPath();
            context.fillStyle = `rgba(165, 243, 252, ${alpha.toFixed(3)})`;
            context.shadowColor = particle.clustered ? "rgba(34, 211, 238, 0.3)" : "rgba(34, 211, 238, 0.14)";
            context.shadowBlur = particle.clustered ? 18 : 8;
            context.arc(particle.x, particle.y, particle.radius * particle.z, 0, Math.PI * 2);
            context.fill();
        });
        context.shadowBlur = 0;

        rafId = window.requestAnimationFrame(draw);
    };

    const updatePointer = (clientX, clientY) => {
        const rect = scene.getBoundingClientRect();
        pointerX = ((clientX - rect.left) / rect.width - 0.5) * 2;
        pointerY = ((clientY - rect.top) / rect.height - 0.5) * 2;
    };

    window.addEventListener("mousemove", (event) => updatePointer(event.clientX, event.clientY), { passive: true });
    window.addEventListener("mouseleave", () => {
        pointerX = 0;
        pointerY = 0;
    });
    window.addEventListener("touchmove", (event) => {
        const touch = event.touches?.[0];
        if (!touch) return;
        updatePointer(touch.clientX, touch.clientY);
    }, { passive: true });
    window.addEventListener("touchend", () => {
        pointerX = 0;
        pointerY = 0;
    }, { passive: true });

    resize();
    window.addEventListener("resize", resize, { passive: true });
    if (!rafId) {
        rafId = window.requestAnimationFrame(draw);
    }
}

function destroyDashboardChart(canvas) {
    const existing = dashboardCharts.get(canvas);
    if (existing) {
        existing.destroy();
        dashboardCharts.delete(canvas);
    }
}

function currentChartPalette() {
    const styles = getComputedStyle(document.body);
    return {
        text: styles.getPropertyValue("--text-primary").trim() || "#dbe7ff",
        muted: styles.getPropertyValue("--text-muted").trim() || "#a7b8db",
        grid: styles.getPropertyValue("--border").trim() || "rgba(255,255,255,0.08)",
        cyan: "#38bdf8",
        blue: "#4f46e5",
        purple: "#a855f7",
        pink: "#ec4899",
        green: "#34d399",
        amber: "#fbbf24",
        red: "#f87171",
    };
}

function createDashboardChart(canvas, config) {
    if (!canvas || !window.Chart) return;
    destroyDashboardChart(canvas);
    const palette = currentChartPalette();
    const chart = new window.Chart(canvas, {
        ...config,
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    labels: {
                        color: palette.text,
                        usePointStyle: true,
                        boxWidth: 12,
                    },
                },
                tooltip: {
                    backgroundColor: "rgba(15, 23, 42, 0.94)",
                    titleColor: "#ffffff",
                    bodyColor: "#dbe7ff",
                },
            },
            ...(config.type === "doughnut"
                ? {}
                : {
                    scales: {
                        x: {
                            ticks: { color: palette.muted },
                            grid: { color: "transparent" },
                        },
                        y: {
                            ticks: { color: palette.muted },
                            grid: { color: palette.grid },
                        },
                    },
                }),
            ...config.options,
        },
    });
    dashboardCharts.set(canvas, chart);
    markChartShellLoaded(canvas);
}

function initializeAdminDashboardCharts() {
    document.querySelectorAll("[data-admin-dashboard-charts]").forEach((panel) => {
        const stats = JSON.parse(panel.dataset.stats || "{}");
        const weeklySessions = JSON.parse(panel.dataset.weeklySessions || "[]");
        const schedules = JSON.parse(panel.dataset.schedules || "[]");
        const palette = currentChartPalette();

        const attendanceCanvas = panel.querySelector("[data-admin-chart='attendance-overview']");
        if (attendanceCanvas) {
            createDashboardChart(attendanceCanvas, {
                type: "doughnut",
                data: {
                    labels: ["Present", "Absent"],
                    datasets: [{
                        data: [Number(stats.present_count || 0), Number(stats.absent_count || 0)],
                        backgroundColor: [palette.green, palette.red],
                        borderWidth: 0,
                    }],
                },
            });
        }

        const subjectCounts = schedules.reduce((acc, item) => {
            const key = item.subject_name || "Unknown";
            acc[key] = (acc[key] || 0) + 1;
            return acc;
        }, {});
        const subjectCanvas = panel.querySelector("[data-admin-chart='subject-wise']");
        if (subjectCanvas) {
            createDashboardChart(subjectCanvas, {
                type: "bar",
                data: {
                    labels: Object.keys(subjectCounts),
                    datasets: [{
                        label: "Scheduled Sessions",
                        data: Object.values(subjectCounts),
                        backgroundColor: [palette.cyan, palette.blue, palette.purple, palette.pink, palette.green, palette.amber],
                        borderRadius: 12,
                    }],
                },
            });
        }

        const classCanvas = panel.querySelector("[data-admin-chart='class-wise']");
        if (classCanvas) {
            const classStats = Array.isArray(stats.class_wise_stats) ? stats.class_wise_stats : [];
            createDashboardChart(classCanvas, {
                type: "bar",
                data: {
                    labels: classStats.map((item) => item.class_name || "Unknown"),
                    datasets: [
                        {
                            label: "Present + Late",
                            data: classStats.map((item) => Number(item.present_count || 0) + Number(item.late_count || 0)),
                            backgroundColor: palette.cyan,
                            borderRadius: 12,
                        },
                        {
                            label: "Absent",
                            data: classStats.map((item) => Number(item.absent_count || 0)),
                            backgroundColor: palette.red,
                            borderRadius: 12,
                        },
                    ],
                },
                options: {
                    scales: {
                        x: { stacked: true },
                        y: { stacked: true },
                    },
                },
            });
        }

        const statusCanvas = panel.querySelector("[data-admin-chart='status-distribution']");
        if (statusCanvas) {
            createDashboardChart(statusCanvas, {
                type: "doughnut",
                data: {
                    labels: ["Present", "Late", "Absent", "Rejected"],
                    datasets: [{
                        data: [
                            Number(stats.present_count || 0),
                            Number(stats.late_count || 0),
                            Number(stats.absent_count || 0),
                            Number(stats.rejected_count || 0),
                        ],
                        backgroundColor: [palette.green, palette.amber, palette.red, palette.purple],
                        borderWidth: 0,
                    }],
                },
            });
        }

        const trendMap = weeklySessions.reduce((acc, item) => {
            const key = item.session_date || "N/A";
            acc[key] = (acc[key] || 0) + Number(item.present_count || 0) + Number(item.late_count || 0);
            return acc;
        }, {});
        const trendCanvas = panel.querySelector("[data-admin-chart='weekly-trend']");
        if (trendCanvas) {
            createDashboardChart(trendCanvas, {
                type: "line",
                data: {
                    labels: Object.keys(trendMap),
                    datasets: [{
                        label: "Marked Attendance",
                        data: Object.values(trendMap),
                        borderColor: palette.cyan,
                        backgroundColor: "rgba(56, 189, 248, 0.16)",
                        tension: 0.35,
                        fill: true,
                        pointRadius: 4,
                    }],
                },
            });
        }
    });
}

function initializeStudentDashboardCharts() {
    document.querySelectorAll("[data-student-dashboard-charts]").forEach((panel) => {
        const summary = JSON.parse(panel.dataset.summary || "{}");
        const subjectStats = Array.isArray(summary.subject_stats) ? summary.subject_stats : [];
        const history = Array.isArray(summary.history) ? summary.history : [];
        const palette = currentChartPalette();

        const attendanceCanvas = panel.querySelector("[data-student-chart='attendance-overview']");
        if (attendanceCanvas) {
            createDashboardChart(attendanceCanvas, {
                type: "doughnut",
                data: {
                    labels: ["Attended", "Absent"],
                    datasets: [{
                        data: [Number(summary.attended_classes || 0), Number(summary.absent_classes || 0)],
                        backgroundColor: [palette.blue, palette.red],
                        borderWidth: 0,
                    }],
                },
            });
        }

        const subjectCanvas = panel.querySelector("[data-student-chart='subject-wise']");
        if (subjectCanvas) {
            createDashboardChart(subjectCanvas, {
                type: "bar",
                data: {
                    labels: subjectStats.map((item) => item.subject_name),
                    datasets: [{
                        label: "Attendance %",
                        data: subjectStats.map((item) => Number(item.attendance_percentage || 0)),
                        backgroundColor: [palette.green, palette.cyan, palette.purple, palette.pink, palette.amber, palette.blue],
                        borderRadius: 12,
                    }],
                },
                options: {
                    scales: {
                        x: { ticks: { color: palette.muted }, grid: { color: "transparent" } },
                        y: { min: 0, max: 100, ticks: { color: palette.muted }, grid: { color: palette.grid } },
                    },
                },
            });
        }

        const trendMap = history.reduce((acc, item) => {
            const key = item.date || "N/A";
            acc[key] = (acc[key] || 0) + ((item.status === "Present" || item.status === "Late") ? 1 : 0);
            return acc;
        }, {});
        const trendCanvas = panel.querySelector("[data-student-chart='weekly-trend']");
        if (trendCanvas) {
            createDashboardChart(trendCanvas, {
                type: "line",
                data: {
                    labels: Object.keys(trendMap),
                    datasets: [{
                        label: "Classes Attended",
                        data: Object.values(trendMap),
                        borderColor: palette.purple,
                        backgroundColor: "rgba(168, 85, 247, 0.16)",
                        tension: 0.35,
                        fill: true,
                        pointRadius: 4,
                    }],
                },
            });
        }
    });
}

function initializeDashboardCharts() {
    initializeAdminDashboardCharts();
    initializeStudentDashboardCharts();
}

function renderAttendanceCalendar() {
    const calendar = document.getElementById("attendanceCalendar");
    const studentSelect = document.getElementById("calendarStudentSelect");
    if (!calendar) return;

    const rawCalendar = calendar.dataset.calendar;
    if (!rawCalendar) {
        calendar.innerHTML = "<p class='meta-line mb-0'>No calendar data available yet.</p>";
        return;
    }

    const calendarData = JSON.parse(rawCalendar);
    const daysByStudent = calendarData.days || {};
    const studentNames = Object.keys(daysByStudent);
    const selectedStudent = studentSelect?.value || studentNames[0];
    const selectedDays = daysByStudent[selectedStudent] || [];

    if (!selectedDays.length) {
        calendar.innerHTML = "<p class='meta-line mb-0'>Register a student to see the attendance calendar.</p>";
        return;
    }

    calendar.innerHTML = selectedDays
        .map((entry) => {
            const statusClass = `calendar-day-${entry.status}`;
            return `
                <div class="calendar-day ${statusClass}">
                    <span class="calendar-day-number">${entry.day}</span>
                    <span class="calendar-day-label">${escapeHtml(entry.weekday)}</span>
                </div>
            `;
        })
        .join("");
}

async function startCamera() {
    const video = document.getElementById("video");
    const canvas = document.getElementById("canvas");
    const resultMessage = document.getElementById("resultMessage");
    if (!video) return;

    try {
        stopMediaStream(currentStream);

        currentStream = await navigator.mediaDevices.getUserMedia({
            video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: "user" },
            audio: false,
        });

        await attachVideoStream(video, currentStream);
        setCameraWrapperState(video, true);
        syncCanvasToVideo(canvas, video);
        watchStream(currentStream, () => {
            setCameraWrapperState(video, false);
            if (resultMessage) {
                resultMessage.textContent = "Camera stream stopped. Click Start Camera to turn it on again.";
            }
            updatePillStatus("cameraStatus", "Camera", "Stopped", "warning");
        });

        video.onloadedmetadata = () => {
            syncCanvasToVideo(canvas, video);
        };

        if (resultMessage) {
            resultMessage.textContent = "Camera started successfully.";
        }
        updatePillStatus("cameraStatus", "Camera", "Running", "ready");
    } catch (error) {
        setCameraWrapperState(video, false);
        if (resultMessage) {
            resultMessage.textContent = "Could not access camera. Please allow browser camera permission.";
        }
        updatePillStatus("cameraStatus", "Camera", "Blocked", "warning");
        console.error(error);
    }
}

async function captureAndAnalyze() {
    const video = document.getElementById("video");
    const canvas = document.getElementById("canvas");
    const resultName = document.getElementById("resultName");
    const resultEmotion = document.getElementById("resultEmotion");
    const resultLiveness = document.getElementById("resultLiveness");
    const resultMessage = document.getElementById("resultMessage");
    const detectedFaces = document.getElementById("detectedFacesCount");
    const recognizedCount = document.getElementById("recognizedStudentsCount");
    const captureButton = document.getElementById("captureBtn");

    if (!video || !canvas || !video.srcObject) {
        if (resultMessage) {
            resultMessage.textContent = "Please start the camera first.";
        }
        updatePillStatus("cameraStatus", "Camera", "Not Started", "warning");
        return;
    }

    if (video.readyState < 2 || !video.videoWidth || !video.videoHeight) {
        if (resultMessage) {
            resultMessage.textContent = "Camera preview is not ready yet. Wait a moment and try again.";
        }
        updatePillStatus("cameraStatus", "Camera", "Loading", "warning");
        return;
    }

    if (analysisInProgress) {
        if (resultMessage) {
            resultMessage.textContent = "One attendance scan is already running. Please wait for it to finish.";
        }
        return;
    }

    const context = canvas.getContext("2d", { willReadFrequently: true });
    const captureFrame = () => {
        if (canvas.width !== ANALYSIS_CAPTURE_WIDTH) {
            canvas.width = ANALYSIS_CAPTURE_WIDTH;
        }
        if (canvas.height !== ANALYSIS_CAPTURE_HEIGHT) {
            canvas.height = ANALYSIS_CAPTURE_HEIGHT;
        }
        context.drawImage(video, 0, 0, canvas.width, canvas.height);
        return canvas.toDataURL("image/jpeg", 0.82);
    };
    const burstFrames = [];

    for (let index = 0; index < ANALYSIS_FRAME_BURST; index += 1) {
        burstFrames.push(captureFrame());
        if (index < ANALYSIS_FRAME_BURST - 1) {
            await new Promise((resolve) => window.setTimeout(resolve, 120));
        }
    }

    analysisInProgress = true;
    if (captureButton) {
        captureButton.disabled = true;
        captureButton.textContent = "Analyzing...";
    }

    if (resultName) resultName.textContent = "Processing...";
    if (resultEmotion) resultEmotion.textContent = "Processing...";
    if (resultLiveness) resultLiveness.textContent = "Checking...";
    if (detectedFaces) detectedFaces.textContent = "...";
    if (recognizedCount) recognizedCount.textContent = "...";
    if (resultMessage) resultMessage.textContent = "Live verification in progress. Turn your face slightly left or right...";
    renderRecognizedPeople([]);

    // Yield once so the browser can paint the loading state before the heavy request starts.
    await new Promise((resolve) => window.setTimeout(resolve, 0));

    try {
        const response = await fetch("/analyze", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ images: burstFrames }),
        });

        const data = await response.json();
        if (!response.ok || !data.success) {
            throw new Error(data.message || "Analysis failed");
        }

        if (resultName) resultName.textContent = data.name || "Unknown";
        if (resultEmotion) resultEmotion.textContent = data.emotion || "Unknown";
        if (resultLiveness) resultLiveness.textContent = data.liveness_label || "Unknown";
        if (resultMessage) resultMessage.textContent = data.message || "Analysis completed.";
        if (detectedFaces) detectedFaces.textContent = String(data.detected_faces || 0);
        if (recognizedCount) recognizedCount.textContent = String(data.recognized_count || 0);
        renderRecognizedPeople(data.recognized_people || [], data.attendance_marked_names || [], data.already_marked_names || []);

        const speechConfig = getSpeechConfig();
        if (data.attendance_marked) {
            if (Array.isArray(data.attendance_marked_names) && data.attendance_marked_names.length) {
                speakText(`Attendance marked for ${data.attendance_marked_names.join(", ")}`);
            } else {
                speakText(speechConfig?.dataset.attendanceMarked || "Attendance marked successfully");
            }
        } else if (data.message) {
            speakText(data.message);
        }

        updatePillStatus("faceStatus", "Face Detection", data.face_detected ? "Detected" : "Not Detected", data.face_detected ? "ready" : "warning");
        updatePillStatus("recognitionStatus", "Face Recognition", data.recognition_ready ? "Ready" : "Not Ready", data.recognition_ready ? "ready" : "warning");
        updatePillStatus("emotionStatus", "Emotion Engine", data.emotion_ready ? "Ready" : "Not Ready", data.emotion_ready ? "ready" : "warning");
        setEngineErrors(data.recognition_error, data.emotion_error);

        const totalStudents = document.getElementById("totalStudents");
        const presentCount = document.getElementById("presentCount");
        const absentCount = document.getElementById("absentCount");
        const highestStudent = document.getElementById("highestStudent");

        if (totalStudents) totalStudents.textContent = data.stats.total_students;
        if (presentCount) presentCount.textContent = data.stats.present_count;
        if (absentCount) absentCount.textContent = data.stats.absent_count;
        if (highestStudent) highestStudent.textContent = data.stats.highest_student || "N/A";
        updateAttendanceCharts(data.stats.chart_data || []);
        const calendar = document.getElementById("attendanceCalendar");
        if (calendar && data.stats.calendar_data) {
            calendar.dataset.calendar = JSON.stringify(data.stats.calendar_data);
            renderAttendanceCalendar();
        }
    } catch (error) {
        if (resultName) resultName.textContent = "Error";
        if (resultEmotion) resultEmotion.textContent = "Error";
        if (resultLiveness) resultLiveness.textContent = "Error";
        if (detectedFaces) detectedFaces.textContent = "0";
        if (recognizedCount) recognizedCount.textContent = "0";
        if (resultMessage) resultMessage.textContent = error.message || "Server issue.";
        renderRecognizedPeople([]);
        updatePillStatus("faceStatus", "Face Detection", "Unknown", "warning");
        updatePillStatus("recognitionStatus", "Face Recognition", "Waiting", "warning");
        updatePillStatus("emotionStatus", "Emotion Engine", "Waiting", "warning");
        console.error(error);
    } finally {
        analysisInProgress = false;
        if (captureButton) {
            captureButton.disabled = false;
            captureButton.textContent = "Capture & Analyze";
        }
    }
}

async function startRegistrationCamera() {
    const video = document.getElementById("registrationVideo");
    const message = document.getElementById("registrationMessage");
    if (!video) return;

    try {
        stopMediaStream(registrationStream);

        registrationStream = await navigator.mediaDevices.getUserMedia({
            video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: "user" },
            audio: false,
        });

        await attachVideoStream(video, registrationStream);
        setCameraWrapperState(video, true);
        watchStream(registrationStream, () => {
            setCameraWrapperState(video, false);
            if (message) {
                message.textContent = "Registration camera stopped. Click Start Camera and capture again.";
            }
        });

        if (message) {
            message.textContent = "Registration camera started. Capture the student's photo.";
        }
    } catch (error) {
        setCameraWrapperState(video, false);
        if (message) {
            message.textContent = "Could not access the camera for registration.";
        }
        console.error(error);
    }
}

function updateDebugMetric(elementId, label, value, state) {
    updatePillStatus(elementId, label, value, state);
}

async function startDebugCamera() {
    const video = document.getElementById("debugVideo");
    const status = document.getElementById("debugStatus");
    if (!video) return;

    try {
        stopMediaStream(debugStream);

        debugStream = await navigator.mediaDevices.getUserMedia({
            video: { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: "user" },
            audio: false,
        });

        await attachVideoStream(video, debugStream);
        setCameraWrapperState(video, true);
        watchStream(debugStream, () => {
            setCameraWrapperState(video, false);
            if (status) {
                status.textContent = "Debug camera stopped. Start it again to continue testing.";
            }
            updateDebugMetric("debugPermission", "Permission", "Stream Ended", "warning");
        });

        const track = debugStream.getVideoTracks()[0];
        const settings = track ? track.getSettings() : {};
        const actualWidth = settings.width || video.videoWidth || "Unknown";
        const actualHeight = settings.height || video.videoHeight || "Unknown";

        if (status) {
            status.textContent = "Debug camera started successfully.";
        }

        updateDebugMetric("debugResolution", "Stream Resolution", `${actualWidth} x ${actualHeight}`, "ready");
        updateDebugMetric("debugVideoSize", "Rendered Video", `${video.videoWidth || "?"} x ${video.videoHeight || "?"}`, "ready");
        updateDebugMetric("debugCanvasSize", "Canvas Capture", "1280 x 720", "ready");
        updateDebugMetric("debugPermission", "Permission", "Granted", "ready");
    } catch (error) {
        setCameraWrapperState(video, false);
        if (status) {
            status.textContent = "Could not access the webcam for debugging.";
        }
        updateDebugMetric("debugPermission", "Permission", "Blocked", "warning");
        console.error(error);
    }
}

function captureDebugFrame() {
    const video = document.getElementById("debugVideo");
    const canvas = document.getElementById("debugCanvas");
    const preview = document.getElementById("debugPreview");
    const status = document.getElementById("debugStatus");
    if (!video || !canvas || !preview || !video.srcObject) {
        if (status) {
            status.textContent = "Start the debug camera before capturing.";
        }
        return;
    }

    const context = canvas.getContext("2d");
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    const imageData = canvas.toDataURL("image/jpeg", 0.95);

    preview.src = imageData;
    updateDebugMetric("debugVideoSize", "Rendered Video", `${video.videoWidth || "?"} x ${video.videoHeight || "?"}`, "ready");
    updateDebugMetric("debugCanvasSize", "Canvas Capture", `${canvas.width} x ${canvas.height}`, "ready");

    if (status) {
        status.textContent = "Captured a test frame. Compare the saved preview with your live face.";
    }
}

function captureRegistrationPhoto() {
    const video = document.getElementById("registrationVideo");
    const canvas = document.getElementById("registrationCanvas");
    const hiddenInput = document.getElementById("registrationPhotoData");
    const preview = document.getElementById("registrationPreview");
    const message = document.getElementById("registrationMessage");

    if (!video || !canvas || !video.srcObject) {
        if (message) {
            message.textContent = "Start the camera before capturing the registration photo.";
        }
        return;
    }

    syncCanvasToVideo(canvas, video);
    const context = canvas.getContext("2d");
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    const imageData = canvas.toDataURL("image/jpeg", 0.92);

    if (hiddenInput) hiddenInput.value = imageData;
    if (preview) {
        preview.src = imageData;
        preview.style.display = "block";
    }
    if (message) {
        message.textContent = "Photo captured successfully. You can now register the student.";
    }
}

function createRecognitionInstance() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) return null;

    const recognition = new SpeechRecognition();
    recognition.lang = "en-US";
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;
    return recognition;
}

function startVoiceInput() {
    const input = document.getElementById("userInput");
    const voiceBtn = document.getElementById("voiceInputBtn");
    if (!input || !voiceBtn) return;

    if (!recognitionInstance) {
        recognitionInstance = createRecognitionInstance();
    }

    if (!recognitionInstance) {
        speakText("Voice input is not supported in this browser.");
        return;
    }

    voiceBtn.textContent = "Listening...";

    recognitionInstance.onresult = (event) => {
        const transcript = event.results?.[0]?.[0]?.transcript || "";
        input.value = transcript.trim();
        voiceBtn.textContent = "🎤";
        sendMessage();
    };

    recognitionInstance.onerror = () => {
        voiceBtn.textContent = "🎤";
        speakText("I could not hear you clearly. Please try again.");
    };

    recognitionInstance.onend = () => {
        voiceBtn.textContent = "🎤";
    };

    recognitionInstance.start();
}

function startAssistantVoiceInput(card) {
    if (!card) return;

    const input = card.querySelector(".assistant-query-input");
    const voiceBtn = card.querySelector(".assistant-voice-btn");
    if (!input || !voiceBtn) return;

    const idleHtml = voiceBtn.dataset.idleHtml || '<i class="bi bi-mic-fill"></i>';
    const listeningHtml = voiceBtn.dataset.listeningHtml || `${idleHtml}<span class="assistant-voice-state">Listening</span>`;

    const recognition = createRecognitionInstance();
    if (!recognition) {
        speakText("Voice input is not supported in this browser.");
        return;
    }

    voiceBtn.disabled = true;
    voiceBtn.innerHTML = listeningHtml;

    recognition.onresult = (event) => {
        const transcript = event.results?.[0]?.[0]?.transcript || "";
        input.value = transcript.trim();
        if (input.value.trim()) {
            submitAssistantQuery(card, input.value.trim());
        }
    };

    recognition.onerror = () => {
        speakText("I could not hear you clearly. Please try again.");
    };

    recognition.onend = () => {
        voiceBtn.disabled = false;
        voiceBtn.innerHTML = idleHtml;
    };

    recognition.start();
}

function formatCountdown(seconds) {
    const safeSeconds = Math.max(0, Math.floor(seconds));
    const hours = String(Math.floor(safeSeconds / 3600)).padStart(2, "0");
    const minutes = String(Math.floor((safeSeconds % 3600) / 60)).padStart(2, "0");
    const secs = String(safeSeconds % 60).padStart(2, "0");
    return `${hours}:${minutes}:${secs}`;
}

function getCountdownElements(card) {
    return {
        badge: card?.querySelector("[data-countdown-badge]"),
        text: card?.querySelector("[data-countdown-text]"),
    };
}

function getNumericDatasetValue(value) {
    const numericValue = Number(value);
    return Number.isFinite(numericValue) ? numericValue : null;
}

function syncCountdownCardsForSession(sessionId, tracking = {}) {
    const normalizedSessionId = normalizeSessionId(sessionId || tracking?.session_id);
    if (!normalizedSessionId) return;

    const attendanceStatus = String(tracking?.attendance_status || tracking?.status || "").trim();
    document.querySelectorAll("[data-countdown-card]").forEach((card) => {
        if (normalizeSessionId(card.dataset.sessionId) !== normalizedSessionId) return;

        card.dataset.attendanceMarked = attendanceStatus ? "true" : "false";
        if (tracking?.attendance_status != null) {
            card.dataset.attendanceWorkflow = String(tracking.attendance_status || "");
        }
        if (tracking?.tracking_state != null) {
            card.dataset.trackingStatus = String(tracking.tracking_state || "");
        }
        if (tracking?.tracking_expires_at != null) {
            card.dataset.trackingExpiresAt = String(tracking.tracking_expires_at || "");
        }
        if (tracking?.phase != null) {
            card.dataset.sessionPhase = String(tracking.phase || "");
        }
        if (tracking?.attendance_seconds_left != null) {
            card.dataset.attendanceSecondsLeft = String(tracking.attendance_seconds_left);
        }
        if (tracking?.gps_seconds_left != null) {
            card.dataset.gpsSecondsLeft = String(tracking.gps_seconds_left);
        }
        updateCountdownCard(card);
    });
}

function updateCountdownCard(card) {
    if (!card) return;

    const { badge, text } = getCountdownElements(card);
    const sessionStatus = String(card.dataset.sessionStatus || "Scheduled");
    const marked = card.dataset.attendanceMarked === "true";
    const sessionDate = card.dataset.sessionDate;
    const openTime = card.dataset.openTime;
    const closeTime = card.dataset.closeTime;
    const startTime = card.dataset.startTime;
    const endTime = card.dataset.endTime;
    const controlTargetId = card.dataset.controlTarget;
    const controlTarget = controlTargetId ? document.getElementById(controlTargetId) : null;
    const cardSessionId = normalizeSessionId(card.dataset.sessionId);
    const controlSessionId = normalizeSessionId(controlTarget?.dataset.sessionId);
    const controlSessionLocked = controlTarget?.dataset.sessionLocked === "true";
    const canUpdateControlTarget = Boolean(
        controlTarget
        && (
            !controlSessionLocked
            || !controlSessionId
            || !cardSessionId
            || controlSessionId === cardSessionId
            || controlTarget.dataset.attendanceState === "marked"
        )
    );
    const attendanceWorkflow = String(card.dataset.attendanceWorkflow || "");
    const workflowStatus = attendanceWorkflow.trim().toUpperCase();
    const trackingStatus = String(card.dataset.trackingStatus || "");
    const trackingExpiresAt = String(card.dataset.trackingExpiresAt || "");
    const sessionPhase = String(card.dataset.sessionPhase || "").trim().toUpperCase();
    const effectiveMarked = marked && sessionPhase !== "UPCOMING";
    const attendanceSecondsLeft = getNumericDatasetValue(card.dataset.attendanceSecondsLeft);
    const gpsSecondsLeft = getNumericDatasetValue(card.dataset.gpsSecondsLeft);
    const now = sessionDate && openTime && closeTime ? new Date() : null;
    const openDate = now ? new Date(`${sessionDate}T${openTime}`) : null;
    const closeDate = now ? new Date(`${sessionDate}T${closeTime}`) : null;
    const attendanceWindowOpen = Boolean(
        now
        && openDate
        && closeDate
        && Number.isFinite(openDate.getTime())
        && Number.isFinite(closeDate.getTime())
        && now >= openDate
        && now <= closeDate
    );

    if (badge) {
        badge.classList.remove("countdown-badge-upcoming", "countdown-badge-open", "countdown-badge-closed", "countdown-badge-marked");
    }
    if (card) {
        card.classList.remove("countdown-open", "countdown-upcoming", "countdown-closed", "countdown-marked");
    }

    if (sessionPhase === "ATTENDANCE_OPEN") {
        const remainingSeconds = closeDate && Number.isFinite(closeDate.getTime())
            ? Math.max(0, Math.floor((closeDate.getTime() - Date.now()) / 1000))
            : Math.max(0, Math.floor(attendanceSecondsLeft || 0));
        if (badge) {
            badge.textContent = "Open";
            badge.classList.add("countdown-badge-open");
        }
        if (text) {
            text.textContent = `Attendance closes in: ${formatCountdown(remainingSeconds)}`;
        }
        card.classList.add("countdown-open");
        if (canUpdateControlTarget) {
            if (effectiveMarked && ["MARKED_PENDING_TRACKING", "TRACKING_ACTIVE", "PROVISIONAL"].includes(workflowStatus)) {
                controlTarget.textContent = "Temporarily Marked";
                controlTarget.title = "Attendance is temporary until GPS verification completes";
                controlTarget.dataset.attendanceOpen = "false";
                controlTarget.dataset.attendanceState = "marked";
                controlTarget.classList.add("is-closed-state");
            } else {
                controlTarget.textContent = "Mark Attendance";
                controlTarget.title = "Attendance window is open";
                controlTarget.dataset.attendanceOpen = "true";
                controlTarget.dataset.attendanceState = "open";
                controlTarget.classList.remove("is-closed-state");
            }
        }
        return;
    }

    if (effectiveMarked && sessionPhase === "GPS_TRACKING") {
        const expiresAtMs = Date.parse(String(trackingExpiresAt).replace(" ", "T"));
        const remainingSeconds = Number.isFinite(expiresAtMs)
            ? Math.max(0, Math.floor((expiresAtMs - Date.now()) / 1000))
            : Math.max(0, Math.floor(gpsSecondsLeft || 0));
        if (badge) {
            badge.textContent = "Running";
            badge.classList.add("countdown-badge-open");
        }
        if (text) {
            text.textContent = `Tracking Time Left: ${formatCountdown(remainingSeconds)}`;
        }
        card.classList.add("countdown-open");
        if (canUpdateControlTarget) {
            controlTarget.textContent = "Temporarily Marked";
            controlTarget.title = "Attendance is temporary until GPS verification completes";
            controlTarget.dataset.attendanceOpen = "false";
            controlTarget.dataset.attendanceState = "marked";
            controlTarget.classList.add("is-closed-state");
        }
        return;
    }

    if (effectiveMarked) {
        if (badge) {
            badge.textContent = workflowStatus === "FINALIZED"
                ? "Verified"
                : workflowStatus === "CANCELLED" || workflowStatus === "REJECTED"
                    ? "Failed"
                    : workflowStatus === "MARKED_PENDING_TRACKING" || workflowStatus === "TRACKING_ACTIVE" || workflowStatus === "PROVISIONAL"
                        ? "Waiting"
                        : "Marked";
            badge.classList.add("countdown-badge-marked");
        }
        if (text) {
            text.textContent = workflowStatus === "CANCELLED" || workflowStatus === "REJECTED"
                ? "Attendance cancelled after GPS verification failed"
                : workflowStatus === "FINALIZED" || workflowStatus === "FINAL"
                    ? "Attendance Marked Successfully"
                    : workflowStatus === "MARKED_PENDING_TRACKING" || workflowStatus === "TRACKING_ACTIVE" || workflowStatus === "PROVISIONAL"
                        ? "Attendance temporarily marked. GPS tracking will start after the attendance window closes."
                        : "Attendance already marked";
        }
        card.classList.add("countdown-marked");
        if (canUpdateControlTarget) {
            controlTarget.textContent = ["MARKED_PENDING_TRACKING", "TRACKING_ACTIVE", "PROVISIONAL"].includes(workflowStatus)
                ? "Temporarily Marked"
                : "Attendance Marked";
            controlTarget.title = ["MARKED_PENDING_TRACKING", "TRACKING_ACTIVE", "PROVISIONAL"].includes(workflowStatus)
                ? "Attendance is temporary until GPS verification completes"
                : "Attendance already marked";
            controlTarget.dataset.attendanceOpen = "false";
            controlTarget.dataset.attendanceState = "marked";
            controlTarget.classList.add("is-closed-state");
        }
        return;
    }

    if (!sessionDate || !openTime || !closeTime) {
        if (badge) {
            badge.textContent = "Closed";
            badge.classList.add("countdown-badge-closed");
        }
        if (text) text.textContent = "Attendance window closed";
        card.classList.add("countdown-closed");
        if (canUpdateControlTarget) {
            controlTarget.textContent = "Attendance Is Closed Now";
            controlTarget.title = "Attendance is closed now";
            controlTarget.dataset.attendanceOpen = "false";
            controlTarget.dataset.attendanceState = "closed";
            controlTarget.classList.add("is-closed-state");
        }
        return;
    }

    const currentTime = now || new Date();
    const computedOpenDate = openDate || new Date(`${sessionDate}T${openTime}`);
    const computedCloseDate = closeDate || new Date(`${sessionDate}T${closeTime}`);
    const startDate = startTime ? new Date(`${sessionDate}T${startTime}`) : computedOpenDate;
    const endDate = endTime ? new Date(`${sessionDate}T${endTime}`) : computedCloseDate;
    const classCancelled = sessionStatus === "Cancelled";
    const classCompleted = sessionStatus === "Completed" || currentTime > endDate;

    let nextState = "closed";
    let nextText = "Attendance window closed";
    let nextBadge = "Closed";
    let enableMark = false;

    if (classCancelled) {
        nextState = "closed";
        nextText = "Attendance window closed";
        nextBadge = "Closed";
    } else if (classCompleted && currentTime > computedCloseDate) {
        nextState = "closed";
        nextText = "Attendance window closed";
        nextBadge = "Closed";
    } else if (currentTime < computedOpenDate) {
        nextState = "upcoming";
        nextText = `Attendance opens in: ${formatCountdown((computedOpenDate.getTime() - currentTime.getTime()) / 1000)}`;
        nextBadge = "Upcoming";
    } else if (currentTime >= computedOpenDate && currentTime <= computedCloseDate && !classCancelled) {
        nextState = "open";
        nextText = `Attendance closes in: ${formatCountdown((computedCloseDate.getTime() - currentTime.getTime()) / 1000)}`;
        nextBadge = "Open";
        enableMark = true;
    }

    if (badge) {
        badge.textContent = nextBadge;
        badge.classList.add(`countdown-badge-${nextState}`);
    }
    if (text) text.textContent = nextText;
    card.classList.add(`countdown-${nextState}`);
    if (canUpdateControlTarget) {
        controlTarget.disabled = false;
        controlTarget.textContent = enableMark ? "Mark Attendance" : "Attendance Is Closed Now";
        controlTarget.title = enableMark ? "Attendance window is open" : "Attendance is closed now";
        controlTarget.dataset.attendanceOpen = enableMark ? "true" : "false";
        controlTarget.dataset.attendanceState = enableMark ? "open" : "closed";
        controlTarget.classList.toggle("is-closed-state", !enableMark);
    }
}

function updateAdminTrackingCountdowns() {
    document.querySelectorAll("[data-admin-tracking-countdown]").forEach((element) => {
        const expiresAt = String(element.dataset.expiresAt || "").trim();
        if (!expiresAt) {
            element.textContent = "Tracking Time Left: N/A";
            return;
        }

        const expiresAtMs = Date.parse(expiresAt.replace(" ", "T"));
        if (!Number.isFinite(expiresAtMs)) {
            element.textContent = "Tracking Time Left: N/A";
            return;
        }

        const remainingSeconds = Math.max(0, Math.floor((expiresAtMs - Date.now()) / 1000));
        element.textContent = `Tracking Time Left: ${formatCountdown(remainingSeconds)}`;
    });
}

async function captureStudentAttendanceFrames() {
    const video = document.getElementById("studentAttendanceVideo");
    const canvas = document.getElementById("studentAttendanceCanvas");
    if (!video || !canvas || !video.srcObject) {
        throw new Error("Start the camera first.");
    }

    const context = canvas.getContext("2d", { willReadFrequently: true });
    const burstFrames = [];
    for (let index = 0; index < ANALYSIS_FRAME_BURST; index += 1) {
        if (canvas.width !== ANALYSIS_CAPTURE_WIDTH) canvas.width = ANALYSIS_CAPTURE_WIDTH;
        if (canvas.height !== ANALYSIS_CAPTURE_HEIGHT) canvas.height = ANALYSIS_CAPTURE_HEIGHT;
        context.drawImage(video, 0, 0, canvas.width, canvas.height);
        burstFrames.push(canvas.toDataURL("image/jpeg", 0.82));
        if (index < ANALYSIS_FRAME_BURST - 1) {
            await new Promise((resolve) => window.setTimeout(resolve, 80));
        }
    }
    return burstFrames;
}

async function getStudentAttendancePosition(purpose = "attendance") {
    if (!navigator.geolocation || typeof navigator.geolocation.getCurrentPosition !== "function") {
        throw createAttendanceGeolocationError("GPS is not available in this browser.", "GPS_TEMP_UNAVAILABLE");
    }

    const getSinglePosition = () => new Promise((resolve, reject) => {
        navigator.geolocation.getCurrentPosition(resolve, reject, GPS_HIGH_ACCURACY_OPTIONS);
    });

    const samples = [];
    const sampleAttempts = 3;
    let lastGeolocationError = null;
    for (let index = 0; index < sampleAttempts; index += 1) {
        try {
            const position = await getSinglePosition();
            samples.push(position);
            if (isFreshGeolocationPosition(position) && Number(position?.coords?.accuracy || 9999) <= 8) {
                break;
            }
        } catch (error) {
            lastGeolocationError = error;
            if (!samples.length) {
                continue;
            }
        }
    }

    if (!samples.length) {
        throw mapAttendanceGeolocationError(lastGeolocationError, purpose);
    }

    const freshSamples = samples.filter((sample) => isFreshGeolocationPosition(sample));
    const candidateSamples = freshSamples.length ? freshSamples : samples;
    const bestSample = candidateSamples.reduce((best, current) => {
        const bestAccuracy = Number(best?.coords?.accuracy || Number.POSITIVE_INFINITY);
        const currentAccuracy = Number(current?.coords?.accuracy || Number.POSITIVE_INFINITY);
        return currentAccuracy < bestAccuracy ? current : best;
    }, candidateSamples[0]);

    if (!isFreshGeolocationPosition(bestSample)) {
        throw createAttendanceGeolocationError(
            "Trying to get a fresh GPS reading...",
            "GPS_LOW_SIGNAL",
        );
    }

    return bestSample;
}

function updateStudentGpsTelemetryUI(data = {}, position = null, tracking = null) {
    const trackingData = tracking && typeof tracking === "object" ? tracking : (attendanceTrackingState || {});
    const distance = data.distance_meters ?? trackingData.distance_meters;
    const minRange = data.allowed_radius_meters ?? trackingData.allowed_radius_meters;

    const gpsText = document.getElementById("studentAttendanceGps");
    if (gpsText) {
        gpsText.textContent = formatStudentGpsStatus(data, position, trackingData);
    }

    const assignments = {
        studentAttendanceDistance: formatMetersValue(distance),
        studentTrackingDistance: formatMetersValue(distance),
        studentTrackingMinRange: formatMetersValue(minRange),
    };

    Object.entries(assignments).forEach(([elementId, text]) => {
        const element = document.getElementById(elementId);
        if (element) element.textContent = text;
    });
}

function clearAttendanceTrackingWatcher() {
    if (navigator.geolocation && attendanceTrackingWatcherId != null) {
        navigator.geolocation.clearWatch(attendanceTrackingWatcherId);
    }
    attendanceTrackingWatcherId = null;
    attendanceTrackingLatestPosition = null;
}

function startAttendanceTrackingWatcher() {
    if (
        attendanceTrackingWatcherId != null
        || !navigator.geolocation
        || typeof navigator.geolocation.watchPosition !== "function"
        || attendanceTrackingState?.tracking_state !== "Tracking Active"
    ) {
        return;
    }

    attendanceTrackingWatcherId = navigator.geolocation.watchPosition(
        (position) => {
            attendanceTrackingLatestPosition = position;
            updateStudentGpsTelemetryUI({}, position, attendanceTrackingState);
        },
        (error) => {
            const geolocationError = mapAttendanceGeolocationError(error, "tracking");
            attendanceTrackingState = {
                ...(attendanceTrackingState || {}),
                gps_state: geolocationError.gpsState,
                gps_status_text: geolocationError.message,
            };
            updateStudentGpsTelemetryUI({ gps_state: geolocationError.gpsState, gps_status_text: geolocationError.message }, null, attendanceTrackingState);
            console.error(error);
        },
        GPS_HIGH_ACCURACY_OPTIONS,
    );
}

function getStudentTrackingCardElements() {
    return {
        card: document.getElementById("studentTrackingStatusCard"),
        badge: document.getElementById("studentTrackingBadge"),
        sessionLabel: document.getElementById("studentTrackingSessionLabel"),
        meta: document.getElementById("studentTrackingMeta"),
        statusText: document.getElementById("studentTrackingStatusText"),
        trackingStateText: document.getElementById("studentTrackingStateText"),
        countdown: document.getElementById("studentTrackingCountdown"),
        message: document.getElementById("studentTrackingMessage"),
        trackingMessage: document.getElementById("studentTrackingTrackerMessage"),
        alert: document.getElementById("studentTrackingAlert"),
        distance: document.getElementById("studentTrackingDistance"),
        minRange: document.getElementById("studentTrackingMinRange"),
        resultAttendance: document.getElementById("studentAttendanceWorkflowStatus"),
        resultStatus: document.getElementById("studentAttendanceTrackingStatus"),
        resultTimer: document.getElementById("studentAttendanceTrackingTimer"),
    };
}

function formatAttendanceWorkflowLabel(status) {
    const normalized = String(status || "").trim().toUpperCase();
    switch (normalized) {
        case "MARKED_PENDING_TRACKING":
        case "PROVISIONAL":
        case "TRACKING_ACTIVE":
            return "Temporarily Marked";
        case "FINALIZED":
        case "FINAL":
            return "Attendance Marked Successfully";
        case "CANCELLED":
        case "REJECTED":
            return "Attendance Cancelled";
        case "":
            return "Not Marked";
        case "NOT MARKED":
            return "Not Marked";
        default:
            return String(status || "");
    }
}

function formatTrackingStateLabel(status) {
    const normalized = String(status || "").trim();
    if (!normalized) return "Not Started";
    if (normalized === "Tracking Not Started" || normalized === "Not Started") {
        return "Not Started";
    }
    if (normalized === "WAITING_FOR_WINDOW_CLOSE") {
        return "Waiting for attendance window to close";
    }
    if (normalized === "Waiting For Attendance Window To Close") {
        return "Waiting for attendance window to close";
    }
    if (normalized === "Tracking Active") {
        return "Running";
    }
    if (normalized === "Tracking Completed" || normalized === "Completed") {
        return "Completed";
    }
    if (normalized === "Attendance Cancelled") {
        return "Failed";
    }
    return normalized;
}

function setTrackingBadgeState(element, status) {
    if (!element) return;
    element.classList.remove("legend-present", "legend-absent", "legend-neutral", "legend-upcoming", "legend-marked");
    const normalized = String(status || "").toLowerCase();
    if (normalized.includes("final")) {
        element.classList.add("legend-present");
    } else if (normalized.includes("tracking_active") || normalized.includes("tracking active")) {
        element.classList.add("legend-present");
    } else if (normalized.includes("marked_pending_tracking") || normalized.includes("waiting")) {
        element.classList.add("legend-upcoming");
    } else if (normalized.includes("provisional")) {
        element.classList.add("legend-upcoming");
    } else if (normalized.includes("cancelled")) {
        element.classList.add("legend-absent");
    } else if (normalized.includes("completed") || normalized.includes("not required")) {
        element.classList.add("legend-neutral");
    } else {
        element.classList.add("legend-upcoming");
    }
}

function setTrackingAlertState(element, status) {
    if (!element) return;
    element.classList.remove("tracking-alert-cancelled", "tracking-alert-success", "tracking-alert-muted");
    const normalized = String(status || "").toLowerCase();
    if (normalized.includes("cancelled")) {
        element.classList.add("tracking-alert-cancelled");
    } else if (normalized.includes("completed") || normalized.includes("final")) {
        element.classList.add("tracking-alert-success");
    } else {
        element.classList.add("tracking-alert-muted");
    }
}

function clearAttendanceTrackingHeartbeat() {
    if (attendanceTrackingHeartbeatHandle) {
        window.clearInterval(attendanceTrackingHeartbeatHandle);
        attendanceTrackingHeartbeatHandle = null;
    }
}

function clearAttendanceTrackingCountdown() {
    if (attendanceTrackingCountdownHandle) {
        window.clearInterval(attendanceTrackingCountdownHandle);
        attendanceTrackingCountdownHandle = null;
    }
}

function clearAttendanceTrackingStatusPoll() {
    if (attendanceTrackingStatusPollHandle) {
        window.clearInterval(attendanceTrackingStatusPollHandle);
        attendanceTrackingStatusPollHandle = null;
    }
}

function updateAttendanceTrackingCountdown() {
    const elements = getStudentTrackingCardElements();
    const tracking = attendanceTrackingState;
    let timerText = "N/A";

    if (tracking?.phase === "GPS_TRACKING" && tracking?.available && tracking.tracking_state === "Tracking Active") {
        const expiresAtMs = tracking.tracking_expires_at
            ? Date.parse(String(tracking.tracking_expires_at).replace(" ", "T"))
            : Number.NaN;
        const remainingSeconds = Number.isFinite(expiresAtMs)
            ? Math.max(0, Math.floor((expiresAtMs - Date.now()) / 1000))
            : Math.max(0, Number(tracking.gps_seconds_left || tracking.remaining_seconds || 0));
        attendanceTrackingState.remaining_seconds = remainingSeconds;
        timerText = formatCountdown(remainingSeconds);
    } else if (tracking?.phase === "ATTENDANCE_OPEN" && tracking?.available && tracking.tracking_state === "Waiting For Attendance Window To Close") {
        timerText = "Starts after attendance closes";
    }

    if (elements.countdown) elements.countdown.textContent = timerText;
    if (elements.resultTimer) elements.resultTimer.textContent = timerText;
}

function refreshStudentSchedulePanel() {
    const studentPanel = document.getElementById("studentWeeklySchedulePanel");
    if (studentPanel?.dataset.selectedDate) {
        loadSchedulePanel(studentPanel, studentPanel.dataset.selectedDate);
    }
}

function renderAttendanceTrackingState(tracking, options = {}) {
    const elements = getStudentTrackingCardElements();
    const previousTracking = attendanceTrackingState || null;
    const previousTrackingSessionId = normalizeSessionId(previousTracking?.session_id);
    const incomingTrackingSessionId = normalizeSessionId(tracking?.session_id);
    const telemetryFallback = previousTrackingSessionId && incomingTrackingSessionId && previousTrackingSessionId === incomingTrackingSessionId
        ? previousTracking
        : null;
    const nextTracking = tracking && typeof tracking === "object"
        ? {
            ...tracking,
            distance_meters: tracking.distance_meters ?? telemetryFallback?.distance_meters ?? null,
            raw_distance_meters: tracking.raw_distance_meters ?? telemetryFallback?.raw_distance_meters ?? null,
            gps_accuracy_meters: tracking.gps_accuracy_meters ?? telemetryFallback?.gps_accuracy_meters ?? null,
            allowed_radius_meters: tracking.allowed_radius_meters ?? telemetryFallback?.allowed_radius_meters ?? null,
            range_state: tracking.range_state ?? telemetryFallback?.range_state ?? "",
        }
        : {
            available: false,
            status: "Not Started",
            attendance_status: "",
            tracking_state: "Not Started",
            gps_state: "GPS_NOT_REQUESTED",
            gps_status_text: "Not captured yet",
            message: "Attendance can be marked during the attendance window. GPS tracking starts after the attendance window closes.",
            tracking_message: "Attendance can be marked during the attendance window. GPS tracking starts after the attendance window closes.",
        };
    const nextSessionId = normalizeSessionId(nextTracking.session_id);
    const currentSessionId = normalizeSessionId(attendanceTrackingState?.session_id || attendanceTrackingFocusSessionId);
    const nextPhase = String(nextTracking.phase || "").trim().toUpperCase();
    const allowSessionReplacement = Boolean(
        !options.force
        && nextSessionId
        && currentSessionId
        && nextSessionId !== currentSessionId
        && ["ATTENDANCE_OPEN", "UPCOMING", "CLOSED"].includes(nextPhase)
    );
    if (!options.force && !nextTracking.available && isAttendanceTrackingLocked(attendanceTrackingState) && !allowSessionReplacement) {
        return;
    }
    if (
        !options.force
        && nextSessionId
        && currentSessionId
        && nextSessionId !== currentSessionId
        && isAttendanceTrackingLocked(attendanceTrackingState)
        && !allowSessionReplacement
    ) {
        return;
    }

    attendanceTrackingState = nextTracking;
    if (nextSessionId) {
        setAttendanceTrackingFocusSessionId(nextSessionId);
    }
    syncCountdownCardsForSession(nextSessionId, nextTracking);
    const hasMarkedAttendance = Boolean(nextTracking.available && nextTracking.attendance_status);
    const formattedAttendanceStatus = hasMarkedAttendance
        ? formatAttendanceWorkflowLabel(nextTracking.attendance_status || nextTracking.status || "Not Marked")
        : "Not Marked";
    const formattedTrackingState = hasMarkedAttendance
        ? formatTrackingStateLabel(nextTracking.tracking_state || nextTracking.tracking_status || "Not Started")
        : "Not Started";
    if (elements.sessionLabel) {
        elements.sessionLabel.textContent = nextTracking.subject_name || "Waiting For Attendance";
    }
    if (elements.meta) {
        if (nextTracking.class_name) {
            const metaParts = [];
            if (nextTracking.class_name) metaParts.push(nextTracking.class_name);
            elements.meta.textContent = metaParts.join(" | ") || "Tracking information available.";
        } else {
            elements.meta.textContent = "Attendance can be marked during the attendance window. GPS tracking starts after the attendance window closes.";
        }
    }
    if (elements.badge) {
        elements.badge.textContent = formattedAttendanceStatus;
        setTrackingBadgeState(elements.badge, hasMarkedAttendance ? (nextTracking.attendance_status || nextTracking.status) : "");
    }
    if (elements.statusText) elements.statusText.textContent = formattedAttendanceStatus;
    if (elements.trackingStateText) elements.trackingStateText.textContent = formattedTrackingState;
    if (elements.message) {
        elements.message.textContent = hasMarkedAttendance
            ? (nextTracking.message || "")
            : "GPS tracking starts only after attendance is marked and the attendance window closes.";
    }
    if (elements.trackingMessage) {
        elements.trackingMessage.textContent = hasMarkedAttendance
            ? (nextTracking.tracking_message || "")
            : "Tracker waits until attendance is marked and the attendance window closes.";
    }
    if (elements.alert) {
        elements.alert.hidden = !hasMarkedAttendance;
        elements.alert.textContent = hasMarkedAttendance ? (nextTracking.message || "") : "";
        setTrackingAlertState(elements.alert, hasMarkedAttendance ? (nextTracking.attendance_status || nextTracking.status) : "");
    }
    if (elements.resultAttendance) {
        elements.resultAttendance.textContent = formattedAttendanceStatus;
    }
    if (elements.resultStatus) {
        elements.resultStatus.textContent = formattedTrackingState;
    }
    updateStudentGpsTelemetryUI({}, attendanceTrackingLatestPosition, nextTracking);

    clearAttendanceTrackingCountdown();
    clearAttendanceTrackingStatusPoll();
    updateAttendanceTrackingCountdown();
    if (nextTracking.available && nextTracking.tracking_state === "Tracking Active") {
        attendanceTrackingCountdownHandle = window.setInterval(updateAttendanceTrackingCountdown, 1000);
        attendanceTrackingStatusPollHandle = window.setInterval(() => {
            restoreAttendanceTrackingState(nextTracking.session_id || "");
        }, TRACKING_STATUS_POLL_INTERVAL_MS);
    } else if (nextTracking.available && nextTracking.tracking_state === "Waiting For Attendance Window To Close") {
        attendanceTrackingCountdownHandle = window.setInterval(updateAttendanceTrackingCountdown, 1000);
        attendanceTrackingStatusPollHandle = window.setInterval(() => {
            restoreAttendanceTrackingState(nextTracking.session_id || "");
        }, TRACKING_STATUS_POLL_INTERVAL_MS);
    }

    clearAttendanceTrackingHeartbeat();
    clearAttendanceTrackingWatcher();
    if (nextTracking.available && nextTracking.tracking_state === "Tracking Active") {
        startAttendanceTrackingWatcher();
        attendanceTrackingHeartbeatHandle = window.setInterval(() => {
            sendAttendanceTrackingHeartbeat();
        }, TRACKING_HEARTBEAT_INTERVAL_MS);
    }

    const previousAttendanceStatus = String(previousTracking?.attendance_status || previousTracking?.status || "").trim().toUpperCase();
    const nextAttendanceStatus = String(nextTracking.attendance_status || nextTracking.status || "").trim().toUpperCase();
    if (previousTracking && nextAttendanceStatus === "FINALIZED" && previousAttendanceStatus !== "FINALIZED") {
        triggerAttendanceSuccess();
    }
}

async function restoreAttendanceTrackingState(sessionId = "") {
    const card = document.getElementById("studentTrackingStatusCard");
    const endpoint = card?.dataset.statusEndpoint;
    const requestedSessionId = normalizeSessionId(sessionId || attendanceTrackingFocusSessionId || card?.dataset.focusSessionId);
    if (!endpoint || !requestedSessionId || attendanceTrackingStatusRestoreInFlight) return;

    attendanceTrackingStatusRestoreInFlight = true;
    attendanceTrackingRestoreRequestId += 1;
    const restoreRequestId = attendanceTrackingRestoreRequestId;
    try {
        const previousState = attendanceTrackingState?.tracking_state || "";
        const previousAttendanceStatus = attendanceTrackingState?.attendance_status || "";
        const response = await fetch(`${endpoint}?session_id=${encodeURIComponent(requestedSessionId)}`);
        const data = await response.json();
        if (restoreRequestId !== attendanceTrackingRestoreRequestId) return;
        if (!response.ok || !data.success) {
            throw new Error(data.message || "Could not restore tracking state.");
        }
        renderAttendanceTrackingState(data.tracking);
        if (previousState !== "Tracking Active" && data.tracking?.tracking_state === "Tracking Active") {
            window.setTimeout(() => sendAttendanceTrackingHeartbeat(), 350);
        }
        if (
            previousState !== (data.tracking?.tracking_state || "")
            || previousAttendanceStatus !== (data.tracking?.attendance_status || "")
        ) {
            refreshStudentSchedulePanel();
        }
    } catch (error) {
        console.error(error);
        const status = document.getElementById("studentAttendanceStatus");
        if (status && attendanceTrackingState?.available) {
            status.textContent = "Tracking status refresh is temporarily delayed. Your session is still preserved.";
        }
    } finally {
        attendanceTrackingStatusRestoreInFlight = false;
    }
}

async function sendAttendanceTrackingHeartbeat() {
    const card = document.getElementById("studentTrackingStatusCard");
    const endpoint = card?.dataset.heartbeatEndpoint;
    if (
        !endpoint
        || !attendanceTrackingState?.available
        || attendanceTrackingState.tracking_state !== "Tracking Active"
        || attendanceTrackingHeartbeatInFlight
    ) {
        return;
    }

    attendanceTrackingHeartbeatInFlight = true;
    attendanceTrackingHeartbeatRequestId += 1;
    const heartbeatRequestId = attendanceTrackingHeartbeatRequestId;
    const expectedAttendanceId = attendanceTrackingState?.attendance_id;
    const expectedSessionId = attendanceTrackingState?.session_id;
    let position;
    try {
        if (isFreshGeolocationPosition(attendanceTrackingLatestPosition)) {
            position = attendanceTrackingLatestPosition;
        } else {
            position = await getStudentAttendancePosition("tracking");
            attendanceTrackingLatestPosition = position;
        }
    } catch (error) {
        const geolocationError = mapAttendanceGeolocationError(error, "tracking");
        renderAttendanceTrackingState({
            ...attendanceTrackingState,
            gps_state: geolocationError.gpsState,
            gps_status_text: geolocationError.message,
            message: geolocationError.permissionDenied
                ? geolocationError.message
                : (attendanceTrackingState?.message || "GPS tracking is active and valid readings will be used when available."),
        });
        const status = document.getElementById("studentAttendanceStatus");
        if (status) status.textContent = geolocationError.message;
        return;
    }

    try {
        const response = await fetch(endpoint, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                attendance_id: attendanceTrackingState.attendance_id,
                session_id: attendanceTrackingState.session_id,
                latitude: position.coords.latitude,
                longitude: position.coords.longitude,
                accuracy_meters: position.coords.accuracy,
                position_timestamp_ms: Number(position.timestamp || Date.now()),
            }),
        });
        const data = await response.json();
        if (
            heartbeatRequestId !== attendanceTrackingHeartbeatRequestId
            || expectedAttendanceId !== attendanceTrackingState?.attendance_id
            || expectedSessionId !== attendanceTrackingState?.session_id
        ) {
            return;
        }
        if (!response.ok || !data.success) {
            throw new Error(data.message || "Tracking update failed.");
        }
        const mergedTracking = {
            ...(data.tracking || {}),
            distance_meters: data.distance_meters ?? data.tracking?.distance_meters ?? attendanceTrackingState?.distance_meters ?? null,
            raw_distance_meters: data.raw_distance_meters ?? data.tracking?.raw_distance_meters ?? attendanceTrackingState?.raw_distance_meters ?? null,
            gps_accuracy_meters: data.gps_accuracy_meters ?? data.tracking?.gps_accuracy_meters ?? attendanceTrackingState?.gps_accuracy_meters ?? null,
            allowed_radius_meters: data.allowed_radius_meters ?? data.tracking?.allowed_radius_meters ?? attendanceTrackingState?.allowed_radius_meters ?? null,
            range_state: data.range_state ?? data.tracking?.range_state ?? attendanceTrackingState?.range_state ?? "",
        };
        updateStudentGpsTelemetryUI(data, position, mergedTracking);
        renderAttendanceTrackingState(mergedTracking);
        if (data.tracking?.tracking_state !== "Tracking Active") {
            refreshStudentSchedulePanel();
        }
    } catch (error) {
        console.error(error);
        const status = document.getElementById("studentAttendanceStatus");
        if (status) {
            status.textContent = error.message || "GPS tracking update failed temporarily. The app will retry automatically.";
        }
    } finally {
        attendanceTrackingHeartbeatInFlight = false;
    }
}

function updateStudentAttendanceFields(data, position) {
    const status = document.getElementById("studentAttendanceStatus");
    const emotionText = document.getElementById("studentAttendanceEmotion");
    const livenessText = document.getElementById("studentAttendanceLiveness");
    const subjectText = document.getElementById("studentAttendanceSubject");
    const markButton = document.getElementById("markStudentAttendanceBtn");

    if (status && data?.message) status.textContent = data.message;
    if (emotionText) emotionText.textContent = data?.emotion || "Unknown";
    if (livenessText) livenessText.textContent = data?.liveness_label || "Unknown";
    if (subjectText) subjectText.textContent = data?.subject_name || "N/A";
    updateStudentGpsTelemetryUI(data, position, data?.tracking);
    if (data?.session_id) {
        setAttendanceTrackingFocusSessionId(data.session_id);
        if (markButton) {
            markButton.dataset.sessionId = String(data.session_id);
        }
    }
    if (data?.tracking) {
        renderAttendanceTrackingState(data.tracking);
    }
}

async function submitStudentAttendanceMark(sessionId) {
    const status = document.getElementById("studentAttendanceStatus");
    const button = document.getElementById("markStudentAttendanceBtn");
    const resultBox = document.getElementById("studentAttendanceResultBox");
    if (!sessionId) {
        throw new Error("Capture and analyze your face, liveness, and emotion before marking attendance.");
    }

    if (button) {
        button.disabled = true;
        button.textContent = "Marking...";
    }
    setProcessingState(resultBox, true);
    if (status) {
        status.textContent = "Saving temporary attendance...";
    }

    try {
        const response = await fetch("/mark_attendance", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                session_id: sessionId,
            }),
        });
        const data = await response.json();
        updateStudentAttendanceFields(data, studentAttendanceAnalysisState?.position || null);
        const responseStatus = String(data?.status || data?.tracking?.attendance_status || "").trim().toUpperCase();
        const shouldLockMarkedState = response.status === 409 && (
            Boolean(data?.already_marked)
            || Boolean(data?.tracking?.available)
            || ["MARKED_PENDING_TRACKING", "TRACKING_ACTIVE", "FINALIZED", "FINAL", "CANCELLED"].includes(responseStatus)
        );
        if (shouldLockMarkedState && button) {
            button.dataset.attendanceOpen = "false";
            button.dataset.attendanceState = "marked";
            button.dataset.analysisReady = "false";
            button.dataset.sessionLocked = "true";
            resetStudentAttendanceAnalysisState();
        }
        if (!response.ok || !data.success) {
            throw new Error(data.message || "Attendance failed.");
        }

        syncCountdownCardsForSession(data.session_id || sessionId, data.tracking || {});
        if (button) {
            button.textContent = "Temporarily Marked";
            button.dataset.attendanceOpen = "false";
            button.dataset.attendanceState = "marked";
            button.dataset.analysisReady = "false";
            button.dataset.sessionLocked = "true";
        }
        resetStudentAttendanceAnalysisState();
        startCountdownTicker();
        const studentPanel = document.getElementById("studentWeeklySchedulePanel");
        if (studentPanel?.dataset.selectedDate) {
            loadSchedulePanel(studentPanel, studentPanel.dataset.selectedDate);
        }
        return data;
    } finally {
        setProcessingState(resultBox, false);
        if (button) {
            button.disabled = false;
            if (button.dataset.attendanceState === "marked") {
                button.textContent = "Temporarily Marked";
            } else if (button.dataset.attendanceOpen === "true") {
                button.textContent = button.dataset.defaultLabel || "Mark Attendance";
            } else {
                button.textContent = button.dataset.closedLabel || "Attendance Is Closed Now";
            }
        }
    }
}

function startCountdownTicker() {
    if (countdownTimerHandle) {
        window.clearInterval(countdownTimerHandle);
    }
    const updateAll = () => {
        document.querySelectorAll("[data-countdown-card]").forEach((card) => updateCountdownCard(card));
        updateAdminTrackingCountdowns();
    };
    updateAll();
    countdownTimerHandle = window.setInterval(updateAll, 1000);
}

function formatScheduleBadgeClass(status) {
    switch (String(status || "").toLowerCase()) {
        case "active":
        case "open":
            return "legend-present";
        case "cancelled":
        case "closed":
            return "legend-absent";
        case "completed":
            return "legend-neutral";
        case "attendance marked":
            return "legend-marked";
        default:
            return "legend-upcoming";
    }
}

function renderWeekBar(panel, weekDates, selectedDate) {
    const bar = panel.querySelector("[data-week-bar]");
    if (!bar || !Array.isArray(weekDates)) return;

    bar.innerHTML = weekDates
        .map((day) => `
            <button class="week-day-btn ${day.iso_date === selectedDate ? "is-selected" : ""}" type="button" data-date="${escapeHtml(day.iso_date)}">
                <span class="week-day-label">${escapeHtml(day.day_short)}</span>
                <span class="week-day-number">${escapeHtml(day.day_number)}</span>
                <span class="week-day-month">${escapeHtml(day.month_short)}</span>
            </button>
        `)
        .join("");
}

function buildScheduleCardHtml(item, role) {
    const isActive = item.class_status === "Active";
    const isCancelled = item.class_status === "Cancelled";
    const cardClasses = ["schedule-card"];
    if (isActive) cardClasses.push("schedule-card-active");
    if (isCancelled) cardClasses.push("schedule-card-cancelled");

    return `
        <details class="${cardClasses.join(" ")}" ${isActive ? "open" : ""}>
            <summary class="schedule-card-summary">
                <div>
                    <div class="schedule-card-title-row">
                        <h4>${escapeHtml(item.subject_name)}</h4>
                        <span class="legend-chip ${formatScheduleBadgeClass(item.class_status)}">${escapeHtml(item.class_status)}</span>
                    </div>
                    <p>${escapeHtml(item.teacher_name)} | ${escapeHtml(item.room_name)} | ${escapeHtml(item.start_time)} - ${escapeHtml(item.end_time)}</p>
                    <div class="schedule-status-row">
                        <span class="legend-chip ${formatScheduleBadgeClass(item.attendance_state)}">${escapeHtml(item.attendance_state)}</span>
                    </div>
                </div>
            </summary>
            <div class="schedule-card-body">
                <div class="schedule-card-grid">
                    <div><strong>Subject:</strong> ${escapeHtml(item.subject_name)}</div>
                    <div><strong>Teacher:</strong> ${escapeHtml(item.teacher_name)}</div>
                    <div><strong>Room:</strong> ${escapeHtml(item.room_name)}</div>
                    <div><strong>Date:</strong> ${escapeHtml(item.session_date)}</div>
                    <div><strong>Class Time:</strong> ${escapeHtml(item.start_time)} - ${escapeHtml(item.end_time)}</div>
                    <div><strong>Attendance Window:</strong> ${escapeHtml(item.attendance_open_time)} - ${escapeHtml(item.late_close_time)}</div>
                    <div><strong>GPS Eligibility:</strong> ${item.gps_enabled ? `Within ${escapeHtml(item.allowed_radius_meters)} m` : "Not configured"}</div>
                    <div><strong>Attendance:</strong> ${escapeHtml(item.attendance_state)}</div>
                    <div><strong>Post Tracking:</strong> ${escapeHtml(item.post_attendance_tracking_minutes)} min</div>
                    ${item.attendance_workflow_status ? `<div><strong>Workflow:</strong> ${escapeHtml(item.attendance_workflow_status)}</div>` : ""}
                    ${item.tracking_status ? `<div><strong>Tracker:</strong> ${escapeHtml(item.tracking_status)}</div>` : ""}
                    ${item.cancellation_reason ? `<div class="schedule-card-full"><strong>Tracker Message:</strong> ${escapeHtml(item.cancellation_reason || item.tracking_status_message || "")}</div>` : item.tracking_status_message ? `<div class="schedule-card-full"><strong>Tracker Message:</strong> ${escapeHtml(item.tracking_status_message)}</div>` : ""}
                    ${item.status_reason ? `<div class="schedule-card-full"><strong>Note:</strong> ${escapeHtml(item.status_reason)}</div>` : ""}
                    ${role === "admin" ? `<div class="schedule-card-full"><strong>Counts:</strong> Present ${escapeHtml(item.present_count)} | Late ${escapeHtml(item.late_count)} | Absent ${escapeHtml(item.absent_count)} | Provisional ${escapeHtml(item.provisional_count || 0)} | Final ${escapeHtml(item.final_count || 0)} | Tracking Active ${escapeHtml(item.tracking_active_count || 0)} | Rejected ${escapeHtml(item.rejected_count)}</div>` : ""}
                </div>
                <div class="countdown-shell schedule-inline-countdown"
                     data-countdown-card
                     data-session-id="${escapeHtml(item.id || "")}"
                     data-session-status="${escapeHtml(item.session_status)}"
                     data-open-time="${escapeHtml(item.attendance_open_time)}"
                     data-close-time="${escapeHtml(item.late_close_time)}"
                     data-start-time="${escapeHtml(item.start_time)}"
                     data-end-time="${escapeHtml(item.end_time)}"
                     data-session-date="${escapeHtml(item.session_date)}"
                     data-attendance-marked="${item.attendance_workflow_status ? "true" : "false"}"
                     data-attendance-workflow="${escapeHtml(item.attendance_workflow_status || "")}"
                     data-session-phase="${escapeHtml(item.session_phase || "")}"
                     data-attendance-seconds-left="${escapeHtml(item.attendance_seconds_left ?? "")}"
                     data-gps-seconds-left="${escapeHtml(item.gps_seconds_left ?? "")}"
                     data-tracking-status="${escapeHtml(item.tracking_status || "")}"
                     data-tracking-expires-at="${escapeHtml(item.tracking_expires_at || "")}">
                    <div class="countdown-shell-header">
                        <div class="countdown-badge" data-countdown-badge>${escapeHtml(item.countdown_state || "Upcoming")}</div>
                    </div>
                    <div class="countdown-text" data-countdown-text>${escapeHtml(item.countdown_text)}</div>
                </div>
            </div>
        </details>
    `;
}

function renderScheduleBody(panel, sessions) {
    const body = panel.querySelector("[data-schedule-body]");
    const filterInput = panel.querySelector("[data-schedule-filter]");
    if (!body) return;

    const filterValue = String(filterInput?.value || "").trim().toLowerCase();
    const filtered = (sessions || []).filter((item) => {
        if (!filterValue) return true;
        return String(item.subject_name || "").toLowerCase().includes(filterValue)
            || String(item.class_name || "").toLowerCase().includes(filterValue)
            || String(item.teacher_name || "").toLowerCase().includes(filterValue);
    });

    if (!filtered.length) {
        body.innerHTML = `<div class="schedule-empty-state">No classes scheduled for this day</div>`;
        return;
    }

    body.innerHTML = `
        <div class="schedule-card-list">
            ${filtered.map((item) => buildScheduleCardHtml(item, panel.dataset.scheduleRole)).join("")}
        </div>
    `;
}

async function loadSchedulePanel(panel, selectedDate) {
    if (!panel) return;

    const body = panel.querySelector("[data-schedule-body]");
    const endpoint = panel.dataset.endpoint;
    if (!endpoint || !body) return;

    panel.dataset.selectedDate = selectedDate;
    body.innerHTML = `
        <div class="schedule-loading">
            <div class="schedule-skeleton"></div>
            <div class="schedule-skeleton"></div>
        </div>
    `;

    try {
        const response = await fetch(`${endpoint}?date=${encodeURIComponent(selectedDate)}`);
        const data = await response.json();
        if (!response.ok || !data.success) {
            throw new Error(data.message || "Could not load schedule.");
        }
        schedulePanelState.set(panel, data.sessions || []);
        renderWeekBar(panel, data.week_dates || [], data.selected_date);
        renderScheduleBody(panel, data.sessions || []);
        startCountdownTicker();
    } catch (error) {
        body.innerHTML = `<div class="schedule-empty-state">${escapeHtml(error.message || "Could not load schedule.")}</div>`;
    }
}

function initializeSchedulePanels() {
    document.querySelectorAll("[data-schedule-role]").forEach((panel) => {
        const selectedDate = panel.dataset.selectedDate;
        const filterInput = panel.querySelector("[data-schedule-filter]");
        const autoRefreshMs = Number(panel.dataset.autoRefreshMs || 0);

        panel.addEventListener("click", (event) => {
            const button = event.target.closest(".week-day-btn");
            if (!button || !panel.contains(button)) return;
            loadSchedulePanel(panel, button.dataset.date);
        });

        if (filterInput) {
            filterInput.addEventListener("input", () => {
                renderScheduleBody(panel, schedulePanelState.get(panel) || []);
                startCountdownTicker();
            });
        }

        if (selectedDate) {
            loadSchedulePanel(panel, selectedDate);
        }

        if (autoRefreshMs > 0) {
            if (panel._autoRefreshHandle) {
                window.clearInterval(panel._autoRefreshHandle);
            }
            panel._autoRefreshHandle = window.setInterval(() => {
                const activeDate = panel.dataset.selectedDate || selectedDate;
                if (activeDate) {
                    loadSchedulePanel(panel, activeDate);
                }
            }, autoRefreshMs);
        }
    });
}

function destroyAssistantChart(container) {
    const existing = assistantCharts.get(container);
    if (existing) {
        existing.destroy();
        assistantCharts.delete(container);
    }
}

function renderAssistantPayload(container, payload) {
    if (!container || !payload) return;

    destroyAssistantChart(container);
    const chunks = [];
    if (payload.message) {
        chunks.push(`<div class="assistant-bubble assistant-bubble-ai">${escapeHtml(payload.message)}</div>`);
    }

    if (payload.table && Array.isArray(payload.table.columns) && Array.isArray(payload.table.rows)) {
        const header = payload.table.columns.map((column) => `<th>${escapeHtml(column)}</th>`).join("");
        const rows = payload.table.rows
            .map((row) => `<tr>${row.map((value) => `<td>${escapeHtml(value)}</td>`).join("")}</tr>`)
            .join("");
        chunks.push(`
            <div class="assistant-rich-content assistant-table-wrap">
                <table class="table assistant-table mb-0">
                    <thead><tr>${header}</tr></thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
        `);
    }

    if (payload.chart) {
        chunks.push(`
            <div class="assistant-rich-content assistant-chart-wrap">
                <div class="meta-line mb-2">${escapeHtml(payload.chart.title || "Attendance Chart")}</div>
                <div class="assistant-chart-box">
                    <canvas class="assistant-chart-canvas"></canvas>
                </div>
            </div>
        `);
    }
    const aiMessage = document.createElement("div");
    aiMessage.className = "assistant-message-row assistant-message-row-ai";
    aiMessage.innerHTML = `
        <div class="assistant-avatar assistant-avatar-ai">AI</div>
        <div class="assistant-message-stack">
            ${chunks.join("")}
            <div class="assistant-meta">Smart Attendance System • ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</div>
        </div>
    `;
    container.appendChild(aiMessage);
    container.scrollTop = container.scrollHeight;
    if (payload.message) {
        speakText(payload.message);
    }

    if (payload.chart && window.Chart) {
        const canvas = aiMessage.querySelector(".assistant-chart-canvas");
        if (canvas) {
            const styles = getComputedStyle(document.body);
            const chartText = styles.getPropertyValue("--text-primary").trim() || "#dbe7ff";
            const chartGrid = styles.getPropertyValue("--border").trim() || "rgba(255,255,255,0.08)";
            const chart = new window.Chart(canvas, {
                type: payload.chart.type || "bar",
                data: {
                    labels: payload.chart.labels || [],
                    datasets: payload.chart.datasets || [],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { labels: { color: chartText } },
                    },
                    scales: {
                        x: {
                            ticks: { color: chartText },
                            grid: { color: chartGrid },
                        },
                        y: {
                            ticks: { color: chartText },
                            grid: { color: chartGrid },
                        },
                    },
                },
            });
            assistantCharts.set(container, chart);
        }
    }
}

async function submitAssistantQuery(card, query) {
    if (!card || !query) return;

    const endpoint = card.dataset.endpoint;
    const responseBox = card.querySelector("[data-assistant-response]");
    const input = card.querySelector(".assistant-query-input");
    const sendButton = card.querySelector(".assistant-send-btn");

    if (responseBox) {
        const userMessage = document.createElement("div");
        userMessage.className = "assistant-message-row assistant-message-row-user";
        userMessage.innerHTML = `
            <div class="assistant-message-stack">
                <div class="assistant-bubble assistant-bubble-user">${escapeHtml(query)}</div>
                <div class="assistant-meta">You • ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</div>
            </div>
            <div class="assistant-avatar assistant-avatar-user">You</div>
        `;
        responseBox.appendChild(userMessage);

        const typingMessage = document.createElement("div");
        typingMessage.className = "assistant-message-row assistant-message-row-ai";
        typingMessage.setAttribute("data-assistant-typing", "true");
        typingMessage.innerHTML = `
            <div class="assistant-avatar assistant-avatar-ai">AI</div>
            <div class="assistant-message-stack">
                <div class="assistant-bubble assistant-bubble-ai">
                    <span class="assistant-typing">
                        <span class="assistant-typing-dot"></span>
                        <span class="assistant-typing-dot"></span>
                        <span class="assistant-typing-dot"></span>
                    </span>
                </div>
                <div class="assistant-meta">Smart Attendance System • thinking...</div>
            </div>
        `;
        responseBox.appendChild(typingMessage);
        responseBox.scrollTop = responseBox.scrollHeight;
    }
    if (sendButton) {
        sendButton.disabled = true;
        sendButton.classList.add("is-sending");
    }
    setProcessingState(card, true);

    try {
        const response = await fetch(endpoint, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message: query }),
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.message || "Assistant request failed.");
        }
        const typingNode = responseBox ? responseBox.querySelector("[data-assistant-typing='true']") : null;
        if (typingNode) {
            typingNode.remove();
        }
        renderAssistantPayload(responseBox, data);
        if (input) input.value = "";
    } catch (error) {
        if (responseBox) {
            const typingNode = responseBox.querySelector("[data-assistant-typing='true']");
            if (typingNode) {
                typingNode.remove();
            }
            renderAssistantPayload(responseBox, { message: error.message || "Assistant request failed." });
        }
        console.error(error);
    } finally {
        if (sendButton) {
            sendButton.disabled = false;
            window.setTimeout(() => sendButton.classList.remove("is-sending"), 450);
        }
        setProcessingState(card, false);
    }
}

function initializeAssistantCards() {
    document.querySelectorAll(".assistant-card").forEach((card) => {
        const input = card.querySelector(".assistant-query-input");
        const sendButton = card.querySelector(".assistant-send-btn");
        const voiceButton = card.querySelector(".assistant-voice-btn");
        const quickButtons = card.querySelectorAll(".assistant-quick-btn");

        if (sendButton && input) {
            sendButton.addEventListener("click", () => submitAssistantQuery(card, input.value.trim()));
            input.addEventListener("keydown", (event) => {
                if (event.key === "Enter") {
                    event.preventDefault();
                    submitAssistantQuery(card, input.value.trim());
                }
            });
        }
        if (voiceButton) {
            voiceButton.addEventListener("click", () => startAssistantVoiceInput(card));
        }

        quickButtons.forEach((button) => {
            button.addEventListener("click", () => submitAssistantQuery(card, button.dataset.query || ""));
        });
    });
}

async function sendMessage() {
    const input = document.getElementById("userInput");
    const chatBox = document.getElementById("chatBox");
    if (!input || !chatBox) return;

    const message = input.value.trim();
    if (!message) return;

    const userDiv = document.createElement("div");
    userDiv.className = "user-message";
    userDiv.textContent = message;
    chatBox.appendChild(userDiv);

    input.value = "";
    chatBox.scrollTop = chatBox.scrollHeight;

    try {
        const response = await fetch("/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message }),
        });

        const data = await response.json();
        const botDiv = document.createElement("div");
        botDiv.className = "bot-message";
        botDiv.textContent = data.message || data.reply || "Smart Attendance System is temporarily unavailable.";
        chatBox.appendChild(botDiv);
        chatBox.scrollTop = chatBox.scrollHeight;
        speakText(data.message || data.reply || "Smart Attendance System is temporarily unavailable.");
    } catch (error) {
        const botDiv = document.createElement("div");
        botDiv.className = "bot-message";
        botDiv.textContent = "Smart Attendance System is temporarily unavailable.";
        chatBox.appendChild(botDiv);
        chatBox.scrollTop = chatBox.scrollHeight;
        speakText("Smart Attendance System is temporarily unavailable.");
        console.error(error);
    }
}

function askQuick(text) {
    const userInput = document.getElementById("userInput");
    if (!userInput) return;
    userInput.value = text;
    sendMessage();
}

function renderAttendanceCharts() {
    const charts = document.querySelectorAll(".attendance-chart");
    charts.forEach((chart) => {
        const rawData = chart.dataset.chart;
        if (!rawData) return;
        updateAttendanceCharts(JSON.parse(rawData));
    });
}

function getPreferredTheme() {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (stored === "dark" || stored === "light") {
        return stored;
    }
    return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function updateThemeToggleLabel(theme) {
    const themeToggleBtn = document.getElementById("themeToggleBtn");
    if (!themeToggleBtn) return;
    themeToggleBtn.textContent = theme === "dark" ? "Light Mode" : "Dark Mode";
    themeToggleBtn.setAttribute("aria-label", `Switch to ${theme === "dark" ? "light" : "dark"} mode`);
}

function applyTheme(theme) {
    document.body.dataset.theme = theme;
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
    updateThemeToggleLabel(theme);
    window.setTimeout(() => {
        initializeDashboardCharts();
        document.querySelectorAll(".assistant-card [data-assistant-response]").forEach((container) => {
            const chartCanvas = container.querySelector(".assistant-chart-canvas");
            if (chartCanvas) {
                const parentCard = container.closest(".assistant-card");
                const lastAiBubble = container.querySelector(".assistant-message-row-ai:last-child");
                if (parentCard && lastAiBubble) {
                    const assistantChart = assistantCharts.get(container);
                    if (assistantChart) {
                        assistantChart.update();
                    }
                }
            }
        });
    }, 40);
}

function initializeThemeToggle() {
    if (!document.body) return;

    applyTheme(getPreferredTheme());

    const themeToggleBtn = document.getElementById("themeToggleBtn");
    if (!themeToggleBtn) return;

    themeToggleBtn.addEventListener("click", () => {
        const nextTheme = document.body.dataset.theme === "light" ? "dark" : "light";
        applyTheme(nextTheme);
    });
}

async function populateLiveLocation(formElement) {
    if (!formElement) {
        throw new Error("Geolocation is not supported in this browser.");
    }

    let position;
    try {
        position = await getCurrentBrowserPosition({
            enableHighAccuracy: true,
            timeout: 15000,
            maximumAge: 0,
        });
    } catch (error) {
        throw new Error(getGeolocationErrorMessage(error));
    }

    const latitudeInput = formElement.querySelector(".gps-latitude-input, [name='gps_latitude']");
    const longitudeInput = formElement.querySelector(".gps-longitude-input, [name='gps_longitude']");
    const sourceInput = formElement.querySelector(".gps-source-input");

    if (!latitudeInput || !longitudeInput) {
        throw new Error("Unable to find latitude and longitude fields.");
    }

    latitudeInput.value = String(position.coords.latitude);
    longitudeInput.value = String(position.coords.longitude);
    latitudeInput.dispatchEvent(new Event("input", { bubbles: true }));
    longitudeInput.dispatchEvent(new Event("input", { bubbles: true }));
    latitudeInput.dispatchEvent(new Event("change", { bubbles: true }));
    longitudeInput.dispatchEvent(new Event("change", { bubbles: true }));
    if (sourceInput) sourceInput.value = "live";
}

function initializeConfirmationActions() {
    document.querySelectorAll(".admin-confirm-action").forEach((button) => {
        button.addEventListener("click", (event) => {
            const message = button.dataset.confirmMessage || "Are you sure you want to continue?";
            if (!window.confirm(message)) {
                event.preventDefault();
            }
        });
    });
}

function initializeResetActions() {
    document.querySelectorAll(".admin-reset-gps-tracking-btn").forEach((button) => {
        button.addEventListener("click", () => {
            const form = button.closest("form");
            if (!form) return;

            const latitudeInput = form.querySelector(".gps-latitude-input");
            const longitudeInput = form.querySelector(".gps-longitude-input");
            const radiusInput = form.querySelector("[name='allowed_radius_meters']");
            const trackingInput = form.querySelector("[name='post_attendance_tracking_minutes']");
            const sourceInput = form.querySelector(".gps-source-input");

            if (latitudeInput) latitudeInput.value = "";
            if (longitudeInput) longitudeInput.value = "";
            if (radiusInput) radiusInput.value = button.dataset.defaultRadius || "60";
            if (trackingInput && button.dataset.defaultTracking) {
                trackingInput.value = button.dataset.defaultTracking;
            }
            if (sourceInput) sourceInput.value = "manual";
        });
    });

    document.querySelectorAll(".admin-reset-tracking-btn").forEach((button) => {
        button.addEventListener("click", () => {
            const form = button.closest("form");
            if (!form) return;
            const trackingInput = form.querySelector("[name='post_attendance_tracking_minutes']");
            if (trackingInput) {
                trackingInput.value = button.dataset.defaultTracking || "0";
            }
        });
    });
}

async function startStudentAttendanceCamera() {
    const video = document.getElementById("studentAttendanceVideo");
    const status = document.getElementById("studentAttendanceStatus");
    if (!video) return;
    if (studentAttendanceCameraStartPromise) {
        return studentAttendanceCameraStartPromise;
    }

    studentAttendanceCameraStartPromise = (async () => {
        try {
            stopMediaStream(studentAttendanceStream);
            studentAttendanceStream = await navigator.mediaDevices.getUserMedia({
                video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: "user" },
                audio: false,
            });
            await attachVideoStream(video, studentAttendanceStream);
            setCameraWrapperState(video, true);
            watchStream(studentAttendanceStream, () => {
                studentAttendanceStream = null;
                setCameraWrapperState(video, false);
                if (status) {
                    status.textContent = "Camera stream stopped. Start the camera again to continue attendance.";
                }
            });
            if (status) {
                status.textContent = "Camera started. Capture will use your current GPS location.";
            }
        } catch (error) {
            stopMediaStream(studentAttendanceStream);
            studentAttendanceStream = null;
            setCameraWrapperState(video, false);
            if (status) {
                status.textContent = "Could not access the camera for student attendance.";
            }
            console.error(error);
        } finally {
            studentAttendanceCameraStartPromise = null;
        }
    })();

    return studentAttendanceCameraStartPromise;
}

function stopStudentAttendanceCamera() {
    const video = document.getElementById("studentAttendanceVideo");
    const status = document.getElementById("studentAttendanceStatus");
    stopMediaStream(studentAttendanceStream);
    studentAttendanceStream = null;
    studentAttendanceCameraStartPromise = null;
    if (video) {
        video.srcObject = null;
        setCameraWrapperState(video, false);
    }
    if (status) {
        status.textContent = "Camera stopped.";
    }
}

async function markStudentAttendance() {
    const button = document.getElementById("markStudentAttendanceBtn");
    const status = document.getElementById("studentAttendanceStatus");
    const sessionId = button?.dataset.sessionId || studentAttendanceAnalysisState?.sessionId || "";
    if (studentAttendanceMarkInFlight) return;

    if (button?.dataset.attendanceOpen !== "true") {
        if (status) status.textContent = "Attendance is closed now.";
        if (button) {
            const closedLabel = button.dataset.closedLabel || "Attendance Is Closed Now";
            const defaultLabel = button.dataset.defaultLabel || "Mark Attendance";
            button.textContent = closedLabel;
            button.classList.add("is-closed-state");
            window.setTimeout(() => {
                if (button.dataset.attendanceOpen !== "true") {
                    button.textContent = closedLabel;
                } else {
                    button.textContent = defaultLabel;
                    button.classList.remove("is-closed-state");
                }
            }, 2200);
        }
        return;
    }

    if (button?.dataset.attendanceState === "marked") {
        if (status) status.textContent = "Attendance is already marked for this class.";
        return;
    }

    if (button?.dataset.analysisReady !== "true" || !sessionId) {
        if (status) {
            status.textContent = "Capture & Analyze first so face, liveness, and emotion are ready.";
        }
        return;
    }

    try {
        studentAttendanceMarkInFlight = true;
        await submitStudentAttendanceMark(sessionId);
    } catch (error) {
        if (status) status.textContent = error.message || "Attendance failed.";
        console.error(error);
    } finally {
        studentAttendanceMarkInFlight = false;
    }
}

async function captureAndAnalyzeStudentAttendance() {
    const status = document.getElementById("studentAttendanceStatus");
    const emotionText = document.getElementById("studentAttendanceEmotion");
    const livenessText = document.getElementById("studentAttendanceLiveness");
    const button = document.getElementById("captureAnalyzeStudentAttendanceBtn");
    const resultBox = document.getElementById("studentAttendanceResultBox");
    const markButton = document.getElementById("markStudentAttendanceBtn");
    if (studentAttendanceAnalysisInFlight) {
        if (status) status.textContent = "Attendance analysis is already running. Please wait.";
        return;
    }
    let burstFrames = [];
    try {
        burstFrames = await captureStudentAttendanceFrames();
    } catch (error) {
        if (status) status.textContent = error.message || "Start the camera first.";
        return;
    }

    if (button) {
        button.disabled = true;
        button.textContent = "Analyzing...";
    }
    studentAttendanceAnalysisInFlight = true;
    studentAttendanceAnalysisRequestId += 1;
    const analysisRequestId = studentAttendanceAnalysisRequestId;
    setProcessingState(resultBox, true);
    if (status) {
        status.textContent = "Analyzing face, emotion, and liveness...";
    }
    if (emotionText) emotionText.textContent = "Analyzing...";
    if (livenessText) livenessText.textContent = "Checking...";
    updateStudentGpsTelemetryUI({ student_lat: null, student_lng: null, distance_meters: null }, null);

    try {
        const response = await fetch("/student-attendance-preview", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                images: burstFrames,
            }),
        });
        const data = await response.json();
        if (analysisRequestId !== studentAttendanceAnalysisRequestId) {
            return;
        }
        updateStudentAttendanceFields(data, null);
        studentAttendanceAnalysisState = {
            ready: Boolean(data.analysis_ready),
            sessionId: String(data.session_id || ""),
            position: null,
        };
        syncStudentAttendanceMarkButton(markButton, data);
        if (!response.ok) {
            return;
        }
    } catch (error) {
        resetStudentAttendanceAnalysisState();
        if (markButton) {
            markButton.dataset.analysisReady = "false";
            markButton.dataset.sessionId = "";
            markButton.dataset.sessionLocked = "false";
        }
        if (status) status.textContent = error.message || "Analysis failed.";
        console.error(error);
    } finally {
        studentAttendanceAnalysisInFlight = false;
        setProcessingState(resultBox, false);
        if (button) {
            button.disabled = false;
            button.textContent = "Capture & Analyze";
        }
    }
}

enableEnhancedMotion();

window.addEventListener("DOMContentLoaded", () => {
    initializeSidebar();
    initializeToasts();
    initializeThemeToggle();

    const speechConfig = getSpeechConfig();
    if (speechConfig) {
        voiceEnabled = speechConfig.dataset.voiceEnabled !== "false";
    }

    const startBtn = document.getElementById("startCameraBtn");
    const captureBtn = document.getElementById("captureBtn");
    const sendBtn = document.getElementById("sendMessageBtn");
    const voiceInputBtn = document.getElementById("voiceInputBtn");
    const userInput = document.getElementById("userInput");
    const startRegistrationBtn = document.getElementById("startRegistrationCameraBtn");
    const captureRegistrationBtn = document.getElementById("captureRegistrationBtn");
    const startDebugBtn = document.getElementById("startDebugCameraBtn");
    const captureDebugBtn = document.getElementById("captureDebugFrameBtn");
    const calendarStudentSelect = document.getElementById("calendarStudentSelect");
    const startStudentAttendanceCameraBtn = document.getElementById("startStudentAttendanceCameraBtn");
    const stopStudentAttendanceCameraBtn = document.getElementById("stopStudentAttendanceCameraBtn");
    const captureAnalyzeStudentAttendanceBtn = document.getElementById("captureAnalyzeStudentAttendanceBtn");
    const markStudentAttendanceBtn = document.getElementById("markStudentAttendanceBtn");
    const adminLiveLocationButtons = document.querySelectorAll(".admin-live-location-btn");

    if (startBtn) startBtn.addEventListener("click", startCamera);
    if (captureBtn) captureBtn.addEventListener("click", captureAndAnalyze);
    if (sendBtn) sendBtn.addEventListener("click", sendMessage);
    if (voiceInputBtn) voiceInputBtn.addEventListener("click", startVoiceInput);
    if (startRegistrationBtn) startRegistrationBtn.addEventListener("click", startRegistrationCamera);
    if (captureRegistrationBtn) captureRegistrationBtn.addEventListener("click", captureRegistrationPhoto);
    if (startDebugBtn) startDebugBtn.addEventListener("click", startDebugCamera);
    if (captureDebugBtn) captureDebugBtn.addEventListener("click", captureDebugFrame);
    if (calendarStudentSelect) calendarStudentSelect.addEventListener("change", renderAttendanceCalendar);
    if (startStudentAttendanceCameraBtn) startStudentAttendanceCameraBtn.addEventListener("click", startStudentAttendanceCamera);
    if (stopStudentAttendanceCameraBtn) stopStudentAttendanceCameraBtn.addEventListener("click", stopStudentAttendanceCamera);
    if (captureAnalyzeStudentAttendanceBtn) captureAnalyzeStudentAttendanceBtn.addEventListener("click", captureAndAnalyzeStudentAttendance);
    if (markStudentAttendanceBtn) markStudentAttendanceBtn.addEventListener("click", markStudentAttendance);
    adminLiveLocationButtons.forEach((button) => {
        button.addEventListener("click", async () => {
            const formElement = button.closest("form");
            const originalLabel = button.textContent;
            button.disabled = true;
            button.textContent = "Fetching Location...";
            try {
                await populateLiveLocation(formElement);
            } catch (error) {
                console.error(error);
                window.alert(error.message || "Could not capture live browser location.");
            } finally {
                button.disabled = false;
                button.textContent = originalLabel;
            }
        });
    });

    if (userInput) {
        userInput.addEventListener("keydown", (event) => {
            if (event.key === "Enter") {
                event.preventDefault();
                sendMessage();
            }
        });
    }

    initializeAssistantCards();
    initializeAccessPortalBackground();
    initializeDashboardCharts();
    initializeSchedulePanels();
    initializeConfirmationActions();
    initializeResetActions();
    initializeRevealSections();
    initializeAnimatedCounters();
    renderAttendanceCharts();
    renderAttendanceCalendar();
    startCountdownTicker();
    resetStudentAttendanceAnalysisState();
    if (markStudentAttendanceBtn) {
        markStudentAttendanceBtn.dataset.analysisReady = "false";
        markStudentAttendanceBtn.dataset.sessionLocked = "false";
        if (!markStudentAttendanceBtn.dataset.sessionId) {
            markStudentAttendanceBtn.dataset.sessionId = "";
        }
    }
    const trackingCard = document.getElementById("studentTrackingStatusCard");
    if (trackingCard?.dataset.initialTracking) {
        try {
            const initialTracking = JSON.parse(trackingCard.dataset.initialTracking);
            const focusSessionId = String(trackingCard.dataset.focusSessionId || initialTracking?.session_id || "");
            if (focusSessionId) {
                setAttendanceTrackingFocusSessionId(focusSessionId);
            }
            renderAttendanceTrackingState(initialTracking, { force: true });
            if (focusSessionId) {
                restoreAttendanceTrackingState(focusSessionId);
            }
            if (initialTracking?.tracking_state === "Tracking Active") {
                window.setTimeout(() => sendAttendanceTrackingHeartbeat(), 1200);
            }
        } catch (error) {
            console.error(error);
        }
    }
    const cleanupStudentAttendanceRuntime = () => {
        clearAttendanceTrackingHeartbeat();
        clearAttendanceTrackingCountdown();
        clearAttendanceTrackingStatusPoll();
        clearAttendanceTrackingWatcher();
        stopStudentAttendanceCamera();
    };
    window.addEventListener("pagehide", cleanupStudentAttendanceRuntime);
    window.addEventListener("beforeunload", cleanupStudentAttendanceRuntime);
    announceFlashMessages();
});
