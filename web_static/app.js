const statusPill = document.getElementById("statusPill");
const tabAsk = document.getElementById("tabAsk");
const tabReview = document.getElementById("tabReview");
const rootInput = document.getElementById("rootInput");
const modelInput = document.getElementById("modelInput");
const questionWrap = document.getElementById("questionWrap");
const questionInput = document.getElementById("questionInput");
const reviewWrap = document.getElementById("reviewWrap");
const targetInput = document.getElementById("targetInput");
const maxFilesWrap = document.getElementById("maxFilesWrap");
const maxFilesInput = document.getElementById("maxFilesInput");
const runBtn = document.getElementById("runBtn");
const copyBtn = document.getElementById("copyBtn");
const metaBox = document.getElementById("metaBox");
const plannerBox = document.getElementById("plannerBox");
const outputBox = document.getElementById("outputBox");
const walletBtn = document.getElementById("walletBtn");
const walletInfo = document.getElementById("walletInfo");
const walletBadge = document.getElementById("walletBadge");
const walletLockOverlay = document.getElementById("walletLockOverlay");
const walletOverlayBtn = document.getElementById("walletOverlayBtn");

const BASE_SEPOLIA_CHAIN_ID = "0x14a34";
const BASE_SEPOLIA_PARAMS = {
  chainId: BASE_SEPOLIA_CHAIN_ID,
  chainName: "Base Sepolia",
  nativeCurrency: { name: "Ether", symbol: "ETH", decimals: 18 },
  rpcUrls: ["https://sepolia.base.org"],
  blockExplorerUrls: ["https://sepolia.basescan.org"],
};

const state = {
  mode: "ask",
  busy: false,
  lastOutput: "",
  walletAddress: "",
  walletChainId: "",
  feeRequired: true,
  feeAmountOpg: "0.0001",
  feeAmountWei: 100000000000000n,
  runsPerFeeTx: 10,
  remainingRuns: 0,
  feeToken: "",
  feeReceiver: "",
  feeChainId: BASE_SEPOLIA_CHAIN_ID,
};

function shortenAddress(addr) {
  if (!addr || addr.length < 10) return addr || "";
  return `${addr.slice(0, 6)}...${addr.slice(-4)}`;
}

function walletMetaSuffix() {
  return state.walletAddress ? ` | Wallet: ${shortenAddress(state.walletAddress)}` : " | Wallet: not connected";
}

function feeMetaSuffix() {
  return ` | Fee: ${state.feeAmountOpg} OPG/${state.runsPerFeeTx} runs | Credits: ${state.remainingRuns}`;
}

function isWalletReady() {
  return Boolean(state.walletAddress) && state.walletChainId === BASE_SEPOLIA_CHAIN_ID;
}

function syncRemainingRuns(raw) {
  const n = Number(raw);
  if (!Number.isFinite(n)) return;
  state.remainingRuns = Math.max(0, Math.floor(n));
}

function refreshAccessGate() {
  const locked = !isWalletReady();
  document.body.classList.toggle("app-locked", locked);
  walletLockOverlay.classList.toggle("hidden", !locked);
  if (locked) {
    runBtn.disabled = true;
    runBtn.textContent = "Connect Wallet First";
  } else if (!state.busy) {
    runBtn.disabled = false;
    runBtn.textContent = "Run AI Analysis";
  }
}

function setWalletBadge(text, mode = "neutral") {
  walletBadge.textContent = text;
  walletBadge.classList.remove("connected", "error");
  if (mode === "connected") walletBadge.classList.add("connected");
  if (mode === "error") walletBadge.classList.add("error");
}

function setWalletInfo(text, good = true) {
  walletInfo.textContent = text;
  walletInfo.style.color = good ? "#475569" : "#991b1b";
}

function applyWalletState(address, chainId) {
  const prevWallet = state.walletAddress;
  state.walletAddress = address || "";
  state.walletChainId = chainId || "";
  if (!state.walletAddress || (prevWallet && prevWallet.toLowerCase() !== state.walletAddress.toLowerCase())) {
    state.remainingRuns = 0;
  }

  const connected = Boolean(state.walletAddress);
  const onBaseSepolia = connected && state.walletChainId === BASE_SEPOLIA_CHAIN_ID;

  if (!connected) {
    walletBtn.textContent = "Connect Wallet";
    setWalletBadge("Not connected", "neutral");
    setWalletInfo(`Network: Base Sepolia (required) | Fee: ${state.feeAmountOpg} OPG per ${state.runsPerFeeTx} runs`, true);
    refreshAccessGate();
    return;
  }

  walletBtn.textContent = "Disconnect";
  if (onBaseSepolia) {
    setWalletBadge(shortenAddress(state.walletAddress), "connected");
    setWalletInfo(
      `Connected on Base Sepolia: ${state.walletAddress} | Credits left: ${state.remainingRuns}`,
      true
    );
  } else {
    setWalletBadge("Wrong network", "error");
    setWalletInfo(`Switch to Base Sepolia. Current chain: ${state.walletChainId || "unknown"}`, false);
  }
  refreshAccessGate();
}

function setMode(mode) {
  state.mode = mode;
  const isAsk = mode === "ask";
  tabAsk.classList.toggle("active", isAsk);
  tabReview.classList.toggle("active", !isAsk);
  questionWrap.classList.toggle("hidden", !isAsk);
  maxFilesWrap.classList.toggle("hidden", !isAsk);
  reviewWrap.classList.toggle("hidden", isAsk);
}

function setBusy(busy) {
  state.busy = busy;
  if (busy) {
    runBtn.disabled = true;
    runBtn.textContent = "AI Running...";
  } else {
    refreshAccessGate();
  }
  outputBox.classList.toggle("loading", busy);
}

function setStatus(text, good = true) {
  statusPill.textContent = text;
  statusPill.style.color = good ? "#065f46" : "#7f1d1d";
  statusPill.style.borderColor = good ? "rgba(13, 148, 136, 0.32)" : "rgba(185, 28, 28, 0.35)";
  statusPill.style.background = good ? "rgba(13, 148, 136, 0.16)" : "rgba(248, 113, 113, 0.16)";
}

function renderPlanner(files, focus) {
  if (!files || files.length === 0) {
    plannerBox.textContent = "Planner files: (none)";
    return;
  }
  const lines = [`Planner focus: ${focus || "n/a"}`, "", "Planner files:", ...files.map((f) => `- ${f}`)];
  plannerBox.textContent = lines.join("\n");
}

function parseAmountToWei(amountText, decimals = 18) {
  const raw = String(amountText || "0").trim();
  if (!/^\d+(\.\d+)?$/.test(raw)) {
    throw new Error(`Invalid fee amount: ${raw}`);
  }
  const [wholeRaw, fracRaw = ""] = raw.split(".");
  const whole = BigInt(wholeRaw || "0");
  const fracPadded = (fracRaw + "0".repeat(decimals)).slice(0, decimals);
  const frac = BigInt(fracPadded || "0");
  return whole * 10n ** BigInt(decimals) + frac;
}

function encodeErc20Transfer(toAddress, amountWei) {
  const method = "a9059cbb";
  const cleanTo = String(toAddress || "").toLowerCase().replace(/^0x/, "");
  if (!/^[0-9a-f]{40}$/.test(cleanTo)) {
    throw new Error("Invalid fee receiver address.");
  }
  const encodedTo = cleanTo.padStart(64, "0");
  const encodedAmount = BigInt(amountWei).toString(16).padStart(64, "0");
  return `0x${method}${encodedTo}${encodedAmount}`;
}

async function waitForReceipt(txHash, timeoutMs = 120000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const receipt = await window.ethereum.request({
      method: "eth_getTransactionReceipt",
      params: [txHash],
    });
    if (receipt) {
      if (receipt.status === "0x1") return receipt;
      throw new Error("Fee transaction failed on-chain.");
    }
    await new Promise((resolve) => setTimeout(resolve, 2200));
  }
  throw new Error("Fee transaction confirmation timeout.");
}

async function payFeeAndGetTxHash() {
  if (!state.feeRequired) return "";
  if (!isWalletReady()) {
    throw new Error("Wallet must be connected on Base Sepolia.");
  }
  if (!state.feeToken || !state.feeReceiver) {
    throw new Error("Fee configuration missing on server.");
  }

  const tx = {
    from: state.walletAddress,
    to: state.feeToken,
    value: "0x0",
    data: encodeErc20Transfer(state.feeReceiver, state.feeAmountWei),
  };

  setStatus(`Paying ${state.feeAmountOpg} OPG for ${state.runsPerFeeTx} runs...`, true);
  const txHash = await window.ethereum.request({
    method: "eth_sendTransaction",
    params: [tx],
  });

  setStatus("Waiting fee confirmation...", true);
  await waitForReceipt(txHash);
  return txHash;
}

async function ensureFeeForRun() {
  if (!state.feeRequired) return "";
  if (state.remainingRuns > 0) {
    state.remainingRuns -= 1;
    setStatus(`Using prepaid credit (${state.remainingRuns} left)...`, true);
    return "";
  }
  return payFeeAndGetTxHash();
}

async function ensureBaseSepolia() {
  if (!window.ethereum) throw new Error("MetaMask is not available.");

  const current = await window.ethereum.request({ method: "eth_chainId" });
  if (current === BASE_SEPOLIA_CHAIN_ID) return current;

  try {
    await window.ethereum.request({
      method: "wallet_switchEthereumChain",
      params: [{ chainId: BASE_SEPOLIA_CHAIN_ID }],
    });
  } catch (switchError) {
    const message = String(switchError?.message || "");
    const shouldAdd = switchError?.code === 4902 || /Unrecognized chain ID/i.test(message);
    if (!shouldAdd) throw switchError;

    await window.ethereum.request({
      method: "wallet_addEthereumChain",
      params: [BASE_SEPOLIA_PARAMS],
    });
    await window.ethereum.request({
      method: "wallet_switchEthereumChain",
      params: [{ chainId: BASE_SEPOLIA_CHAIN_ID }],
    });
  }
  return window.ethereum.request({ method: "eth_chainId" });
}

async function connectWallet() {
  if (!window.ethereum) {
    setWalletBadge("No wallet", "error");
    setWalletInfo("MetaMask not found. Install it to connect.", false);
    refreshAccessGate();
    return;
  }

  if (state.walletAddress) {
    applyWalletState("", "");
    return;
  }

  try {
    walletBtn.disabled = true;
    setWalletInfo("Connecting wallet...", true);

    await ensureBaseSepolia();
    const accounts = await window.ethereum.request({ method: "eth_requestAccounts" });
    const chainId = await window.ethereum.request({ method: "eth_chainId" });
    const account = (accounts && accounts[0]) || "";
    if (!account) throw new Error("No wallet account returned.");
    applyWalletState(account, chainId);
  } catch (err) {
    const msg = String(err?.message || err);
    setWalletBadge("Connect failed", "error");
    setWalletInfo(`Wallet connection failed: ${msg}`, false);
    refreshAccessGate();
  } finally {
    walletBtn.disabled = false;
  }
}

async function handleAccountsChanged(accounts) {
  if (!accounts || accounts.length === 0) {
    applyWalletState("", "");
    return;
  }

  const account = accounts[0];
  let chainId = "";
  try {
    chainId = await window.ethereum.request({ method: "eth_chainId" });
    if (chainId !== BASE_SEPOLIA_CHAIN_ID) {
      chainId = await ensureBaseSepolia();
    }
  } catch (err) {
    const msg = String(err?.message || err);
    setWalletBadge("Wrong network", "error");
    setWalletInfo(`Switch to Base Sepolia: ${msg}`, false);
  }
  applyWalletState(account, chainId);
}

function handleChainChanged(chainId) {
  applyWalletState(state.walletAddress, chainId);
}

async function initWallet() {
  if (!window.ethereum) {
    walletBtn.disabled = true;
    setWalletBadge("No wallet", "error");
    setWalletInfo("MetaMask not detected in this browser.", false);
    refreshAccessGate();
    return;
  }

  window.ethereum.on("accountsChanged", handleAccountsChanged);
  window.ethereum.on("chainChanged", handleChainChanged);

  try {
    const accounts = await window.ethereum.request({ method: "eth_accounts" });
    if (!accounts || accounts.length === 0) {
      applyWalletState("", "");
      return;
    }
    let chainId = await window.ethereum.request({ method: "eth_chainId" });
    if (chainId !== BASE_SEPOLIA_CHAIN_ID) {
      chainId = await ensureBaseSepolia();
    }
    applyWalletState(accounts[0], chainId);
  } catch (err) {
    const msg = String(err?.message || err);
    setWalletBadge("Init failed", "error");
    setWalletInfo(`Wallet init error: ${msg}`, false);
  }
}

async function boot() {
  refreshAccessGate();
  try {
    const res = await fetch("/api/ping");
    const data = await res.json();
    rootInput.value = data.default_root || "";
    state.feeRequired = Boolean(data.fee_required);
    state.feeAmountOpg = String(data.fee_amount_opg || "0.0001");
    state.feeAmountWei = parseAmountToWei(state.feeAmountOpg, 18);
    state.runsPerFeeTx = Math.max(1, Number(data.runs_per_fee_tx || 10));
    state.remainingRuns = 0;
    state.feeToken = String(data.fee_token || "").trim();
    state.feeReceiver = String(data.fee_receiver || "").trim();
    state.feeChainId = String(data.fee_chain_id || BASE_SEPOLIA_CHAIN_ID).toLowerCase();
    setStatus(`Ready: ${data.status}`);
    metaBox.textContent = `Modes: ask, review | Models: ${(data.models || []).join(", ")} | Source: ${data.source_hint || "local path"} | Access: wallet required${feeMetaSuffix()}${walletMetaSuffix()}`;
  } catch (err) {
    setStatus("Backend unreachable", false);
    metaBox.textContent = String(err);
  }
  await initWallet();
}

async function runAsk() {
  const feeTxHash = await ensureFeeForRun();

  const payload = {
    root: rootInput.value.trim(),
    model: modelInput.value.trim(),
    question: questionInput.value.trim(),
    max_files: Number(maxFilesInput.value || 8),
    wallet_address: state.walletAddress || "",
    wallet_chain_id: state.walletChainId || "",
  };
  if (feeTxHash) payload.fee_tx_hash = feeTxHash;

  if (!payload.question) {
    throw new Error("Question is required.");
  }

  const res = await fetch("/api/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (!res.ok || !data.ok) {
    throw new Error(data.error || `Ask failed (${res.status})`);
  }

  syncRemainingRuns(data.remaining_runs);
  setWalletInfo(`Connected on Base Sepolia: ${state.walletAddress} | Credits left: ${state.remainingRuns}`, true);
  metaBox.textContent = `Mode: ask | Model: ${data.model} | Root: ${data.root}${feeMetaSuffix()}${walletMetaSuffix()}`;
  plannerBox.textContent = "Ask completed. Output hidden on website (backend-only).";
  outputBox.textContent = "Ask run successful. Check backend logs/storage for full output.";
  state.lastOutput = "";
}

async function runReview() {
  const feeTxHash = await ensureFeeForRun();

  const payload = {
    root: rootInput.value.trim(),
    model: modelInput.value.trim(),
    target: targetInput.value.trim(),
    wallet_address: state.walletAddress || "",
    wallet_chain_id: state.walletChainId || "",
  };
  if (feeTxHash) payload.fee_tx_hash = feeTxHash;

  const res = await fetch("/api/review", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (!res.ok || !data.ok) {
    throw new Error(data.error || `Review failed (${res.status})`);
  }

  syncRemainingRuns(data.remaining_runs);
  setWalletInfo(`Connected on Base Sepolia: ${state.walletAddress} | Credits left: ${state.remainingRuns}`, true);
  metaBox.textContent = `Mode: review | Model: ${data.model} | Root: ${data.root} | Target: ${data.target || "working tree"}${feeMetaSuffix()}${walletMetaSuffix()}`;
  plannerBox.textContent = data.diff_empty
    ? "Review completed (no diff found). Output hidden on website."
    : "Review completed. Output hidden on website (backend-only).";
  outputBox.textContent = "Review run successful. Check backend logs/storage for full output.";
  state.lastOutput = "";
}

async function handleRun() {
  if (state.busy) return;
  if (!isWalletReady()) {
    setStatus("Wallet required", false);
    outputBox.textContent = "Connect wallet on Base Sepolia first.";
    return;
  }
  setBusy(true);
  setStatus("Running AI...", true);
  outputBox.textContent = "Running model...";
  plannerBox.textContent = "";

  try {
    if (state.mode === "ask") {
      await runAsk();
    } else {
      await runReview();
    }
    setStatus("Done", true);
  } catch (err) {
    const msg = String(err?.message || err);
    if (msg.includes("No remaining runs. fee_tx_hash is required")) {
      state.remainingRuns = 0;
    }
    setStatus("Failed", false);
    plannerBox.textContent = "";
    outputBox.textContent = msg;
  } finally {
    setBusy(false);
  }
}

async function copyOutput() {
  if (!state.lastOutput) return;
  await navigator.clipboard.writeText(state.lastOutput);
  const old = copyBtn.textContent;
  copyBtn.textContent = "Copied";
  setTimeout(() => {
    copyBtn.textContent = old;
  }, 900);
}

tabAsk.addEventListener("click", () => setMode("ask"));
tabReview.addEventListener("click", () => setMode("review"));
runBtn.addEventListener("click", handleRun);
if (copyBtn) {
  copyBtn.style.display = "none";
  copyBtn.addEventListener("click", copyOutput);
}
walletBtn.addEventListener("click", connectWallet);
walletOverlayBtn.addEventListener("click", connectWallet);

setMode("ask");
boot();
