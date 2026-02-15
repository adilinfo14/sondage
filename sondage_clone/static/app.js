const copyBtn = document.getElementById("copyLinkBtn");
const shareInput = document.getElementById("shareUrl");

if (copyBtn && shareInput) {
    copyBtn.addEventListener("click", async () => {
        try {
            await navigator.clipboard.writeText(shareInput.value);
            copyBtn.textContent = "Lien copié ✅";
            setTimeout(() => {
                copyBtn.textContent = "Copier le lien";
            }, 1800);
        } catch {
            shareInput.select();
            document.execCommand("copy");
            copyBtn.textContent = "Lien copié ✅";
            setTimeout(() => {
                copyBtn.textContent = "Copier le lien";
            }, 1800);
        }
    });
}

const voteForm = document.querySelector("form.vote-form[data-vote-status-url]");
const voteEmailInput = voteForm?.querySelector('input[name="participant_email"]');
const voteNameInput = voteForm?.querySelector('input[name="participant_name"]');
const replaceVoteCheckbox = voteForm?.querySelector('input[name="replace_existing_vote"]');
const voteStatusMessage = document.getElementById("voteStatusMessage");

if (voteForm && voteStatusMessage) {
    let lastCheckedIdentity = "";
    let emailAlreadyVoted = false;

    const updateStatusMessage = (text, isError = false) => {
        voteStatusMessage.textContent = text;
        voteStatusMessage.classList.toggle("vote-status-error", isError);
        voteStatusMessage.classList.toggle("vote-status-ok", !isError && !!text);
    };

    const refreshDuplicateMessage = () => {
        if (!emailAlreadyVoted) {
            updateStatusMessage("Aucun vote enregistré pour cet email.");
            return;
        }

        if (replaceVoteCheckbox?.checked) {
            updateStatusMessage("Un vote existe déjà: il sera remplacé à l’enregistrement.");
        } else {
            updateStatusMessage("Ce participant a déjà voté. Coche « Modifier mon vote existant » pour mettre à jour.", true);
        }
    };

    const checkVoteStatus = async () => {
        const email = voteEmailInput ? voteEmailInput.value.trim().toLowerCase() : "";
        const name = voteNameInput ? voteNameInput.value.trim() : "";
        const identity = email || name;

        if (!identity || identity === lastCheckedIdentity) {
            return;
        }
        lastCheckedIdentity = identity;
        emailAlreadyVoted = false;
        updateStatusMessage("Vérification du vote en cours...");

        try {
            const params = new URLSearchParams();
            if (email) {
                params.set("email", email);
            } else if (name) {
                params.set("name", name);
            }
            const url = `${voteForm.dataset.voteStatusUrl}?${params.toString()}`;
            const response = await fetch(url, { method: "GET", headers: { "Accept": "application/json" } });
            if (!response.ok) {
                updateStatusMessage("");
                return;
            }

            const data = await response.json();
            emailAlreadyVoted = !!data.exists;
            refreshDuplicateMessage();
        } catch {
            updateStatusMessage("");
        }
    };

    if (voteEmailInput) {
        voteEmailInput.addEventListener("blur", checkVoteStatus);
        voteEmailInput.addEventListener("change", checkVoteStatus);
        voteEmailInput.addEventListener("input", () => {
            lastCheckedIdentity = "";
            emailAlreadyVoted = false;
            updateStatusMessage("");
        });
    }

    if (voteNameInput) {
        voteNameInput.addEventListener("blur", checkVoteStatus);
        voteNameInput.addEventListener("change", checkVoteStatus);
        voteNameInput.addEventListener("input", () => {
            lastCheckedIdentity = "";
            emailAlreadyVoted = false;
            updateStatusMessage("");
        });
    }

    voteForm.addEventListener("submit", async (event) => {
        await checkVoteStatus();
        if (emailAlreadyVoted && !replaceVoteCheckbox?.checked) {
            event.preventDefault();
            updateStatusMessage("Vote bloqué: cet email a déjà participé. Coche « Modifier mon vote existant ».", true);
        }
    });

    if (replaceVoteCheckbox) {
        replaceVoteCheckbox.addEventListener("change", () => {
            if (emailAlreadyVoted) {
                refreshDuplicateMessage();
            }
        });
    }
}

const createPollForm = document.getElementById("createPollForm");
const previewPollBtn = document.getElementById("previewPollBtn");
const submitPollBtn = document.getElementById("submitPollBtn");
const previewModal = document.getElementById("pollPreviewModal");
const closePreviewModalBtn = document.getElementById("closePreviewModalBtn");

if (createPollForm && previewPollBtn && submitPollBtn && previewModal) {
    let closeTimer = null;
    const titleInput = createPollForm.querySelector('input[name="title"]');
    const creatorInput = createPollForm.querySelector('input[name="creator_name"]');
    const typeInput = createPollForm.querySelector('select[name="poll_type"]');
    const modeInput = createPollForm.querySelector('select[name="response_mode"]');
    const deadlineInput = createPollForm.querySelector('input[name="deadline_at"]');
    const descriptionInput = createPollForm.querySelector('textarea[name="description"]');
    const slotsInput = createPollForm.querySelector('textarea[name="slots"]');

    const previewTitle = document.getElementById("previewTitle");
    const previewCreator = document.getElementById("previewCreator");
    const previewType = document.getElementById("previewType");
    const previewMode = document.getElementById("previewMode");
    const previewDeadline = document.getElementById("previewDeadline");
    const previewDescription = document.getElementById("previewDescription");
    const previewSlots = document.getElementById("previewSlots");

    const openPreviewModal = () => {
        if (closeTimer) {
            clearTimeout(closeTimer);
            closeTimer = null;
        }
        previewModal.hidden = false;
        requestAnimationFrame(() => {
            previewModal.setAttribute("data-open", "true");
        });
        document.body.classList.add("modal-open");
    };

    const closePreviewModal = () => {
        previewModal.setAttribute("data-open", "false");
        closeTimer = setTimeout(() => {
            previewModal.hidden = true;
            closeTimer = null;
        }, 190);
        document.body.classList.remove("modal-open");
    };

    const renderPreview = () => {
        const title = titleInput?.value.trim() || "(Sans titre)";
        const creator = creatorInput?.value.trim() || "(Non défini)";
        const typeLabel = typeInput?.selectedOptions?.[0]?.textContent?.trim() || "-";
        const modeLabel = modeInput?.selectedOptions?.[0]?.textContent?.trim() || "-";
        const deadline = deadlineInput?.value.trim() || "Aucune date limite";
        const description = descriptionInput?.value.trim() || "Aucune description";

        const slots = (slotsInput?.value || "")
            .split("\n")
            .map((item) => item.trim())
            .filter(Boolean)
            .slice(0, 30);

        previewTitle.textContent = `Titre : ${title}`;
        previewCreator.textContent = `Organisateur : ${creator}`;
        previewType.textContent = `Type : ${typeLabel}`;
        previewMode.textContent = `Mode : ${modeLabel}`;
        previewDeadline.textContent = `Date limite : ${deadline}`;
        previewDescription.textContent = `Description : ${description}`;

        previewSlots.innerHTML = "";
        if (slots.length === 0) {
            const li = document.createElement("li");
            li.textContent = "Aucune option détectée";
            previewSlots.appendChild(li);
        } else {
            slots.forEach((slot) => {
                const li = document.createElement("li");
                li.textContent = slot;
                previewSlots.appendChild(li);
            });
        }

        openPreviewModal();
    };

    previewPollBtn.addEventListener("click", () => {
        if (!createPollForm.reportValidity()) {
            return;
        }
        renderPreview();
    });

    if (closePreviewModalBtn) {
        closePreviewModalBtn.addEventListener("click", closePreviewModal);
    }

    previewModal.addEventListener("click", (event) => {
        if (event.target === previewModal) {
            closePreviewModal();
        }
    });

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && !previewModal.hidden) {
            closePreviewModal();
        }
    });
}

const openFeedbackBtn = document.getElementById("openFeedbackBtn");
const feedbackModal = document.getElementById("feedbackModal");
const closeFeedbackModalBtn = document.getElementById("closeFeedbackModalBtn");
const cancelFeedbackBtn = document.getElementById("cancelFeedbackBtn");
const feedbackMessageInput = document.getElementById("feedbackMessageInput");
const feedbackChars = document.getElementById("feedbackChars");

if (openFeedbackBtn && feedbackModal) {
    let feedbackCloseTimer = null;

    const refreshFeedbackCounter = () => {
        if (!feedbackMessageInput || !feedbackChars) {
            return;
        }
        feedbackChars.textContent = `${feedbackMessageInput.value.length} / 1200`;
    };

    const openFeedbackModal = () => {
        if (feedbackCloseTimer) {
            clearTimeout(feedbackCloseTimer);
            feedbackCloseTimer = null;
        }
        feedbackModal.hidden = false;
        requestAnimationFrame(() => {
            feedbackModal.setAttribute("data-open", "true");
        });
        document.body.classList.add("modal-open");
        refreshFeedbackCounter();
        feedbackMessageInput?.focus();
    };

    const closeFeedbackModal = () => {
        feedbackModal.setAttribute("data-open", "false");
        feedbackCloseTimer = setTimeout(() => {
            feedbackModal.hidden = true;
            feedbackCloseTimer = null;
        }, 190);
        document.body.classList.remove("modal-open");
    };

    openFeedbackBtn.addEventListener("click", openFeedbackModal);
    closeFeedbackModalBtn?.addEventListener("click", closeFeedbackModal);
    cancelFeedbackBtn?.addEventListener("click", closeFeedbackModal);

    feedbackModal.addEventListener("click", (event) => {
        if (event.target === feedbackModal) {
            closeFeedbackModal();
        }
    });

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && !feedbackModal.hidden) {
            closeFeedbackModal();
        }
    });

    feedbackMessageInput?.addEventListener("input", refreshFeedbackCounter);
    refreshFeedbackCounter();
}
