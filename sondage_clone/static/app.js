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
