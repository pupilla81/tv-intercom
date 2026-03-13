import { useState, useEffect, useCallback, useRef } from "react";

const CHANNELS = [
  { id: 1, name: "REGIA GEN.", key: "1", color: "#FF6B00", icon: "📡", shortDesc: "Tutti i reparti" },
  { id: 2, name: "CAMERA", key: "2", color: "#00A8FF", icon: "🎥", shortDesc: "Operatori" },
  { id: 3, name: "AUDIO", key: "3", color: "#00E676", icon: "🎙", shortDesc: "Fonici" },
  { id: 4, name: "LUCI", key: "4", color: "#FFD600", icon: "💡", shortDesc: "Gaffer" },
  { id: 5, name: "SCENOGRAFIA", key: "5", color: "#E040FB", icon: "🎨", shortDesc: "Art dept." },
  { id: 6, name: "EMERGENZA", key: "6", color: "#FF1744", icon: "🚨", shortDesc: "Priorità assoluta" },
];

const KEY_MAP = { "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6 };
const NUMPAD_MAP = {
  "Numpad1": 1, "Numpad2": 2, "Numpad3": 3,
  "Numpad4": 4, "Numpad5": 5, "Numpad6": 6
};

export default function IntercomRegia() {
  const [activeChannels, setActiveChannels] = useState(new Set());
  const [pttMode, setPttMode] = useState(true); // true = PTT, false = toggle
  const [log, setLog] = useState([]);
  const [masterMute, setMasterMute] = useState(false);
  const logRef = useRef(null);

  const addLog = useCallback((msg, color) => {
    const time = new Date().toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    setLog(prev => [...prev.slice(-29), { time, msg, color }]);
  }, []);

  const activateChannel = useCallback((id) => {
    if (masterMute) return;
    const ch = CHANNELS.find(c => c.id === id);
    setActiveChannels(prev => new Set([...prev, id]));
    addLog(`TX → ${ch.name}`, ch.color);
  }, [masterMute, addLog]);

  const deactivateChannel = useCallback((id) => {
    const ch = CHANNELS.find(c => c.id === id);
    setActiveChannels(prev => {
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
    addLog(`— ${ch.name} fine trasmissione`, "#666");
  }, [addLog]);

  const toggleChannel = useCallback((id) => {
    setActiveChannels(prev => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
        const ch = CHANNELS.find(c => c.id === id);
        addLog(`— ${ch.name} fine trasmissione`, "#666");
      } else {
        if (masterMute) return prev;
        next.add(id);
        const ch = CHANNELS.find(c => c.id === id);
        addLog(`TX → ${ch.name}`, ch.color);
      }
      return next;
    });
  }, [masterMute, addLog]);

  useEffect(() => {
    const pressed = new Set();

    const onKeyDown = (e) => {
      const id = KEY_MAP[e.key] || NUMPAD_MAP[e.code];
      if (!id || pressed.has(id)) return;
      pressed.add(id);
      if (pttMode) activateChannel(id);
      else toggleChannel(id);
    };

    const onKeyUp = (e) => {
      const id = KEY_MAP[e.key] || NUMPAD_MAP[e.code];
      if (!id) return;
      pressed.delete(id);
      if (pttMode) deactivateChannel(id);
    };

    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
    };
  }, [pttMode, activateChannel, deactivateChannel, toggleChannel]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [log]);

  const isTalking = activeChannels.size > 0;

  return (
    <div style={{
      minHeight: "100vh",
      background: "#0a0a0a",
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      justifyContent: "center",
      fontFamily: "'Courier New', monospace",
      padding: "20px",
      userSelect: "none",
    }}>
      {/* Header */}
      <div style={{ textAlign: "center", marginBottom: 24 }}>
        <div style={{
          fontSize: 11, letterSpacing: 6, color: "#555", marginBottom: 4,
          textTransform: "uppercase"
        }}>
          PRODUZIONE TELEVISIVA
        </div>
        <div style={{
          fontSize: 28, fontWeight: "bold", letterSpacing: 3,
          color: "#e0e0e0", textTransform: "uppercase",
        }}>
          INTERCOM REGIA
        </div>
        <div style={{
          display: "flex", gap: 16, justifyContent: "center",
          marginTop: 10, alignItems: "center"
        }}>
          {/* On air indicator */}
          <div style={{
            display: "flex", alignItems: "center", gap: 6,
            background: isTalking ? "#FF6B00" : "#1a1a1a",
            border: `1px solid ${isTalking ? "#FF6B00" : "#333"}`,
            borderRadius: 4, padding: "4px 12px",
            transition: "all 0.1s",
            boxShadow: isTalking ? "0 0 20px #FF6B0066" : "none",
          }}>
            <div style={{
              width: 8, height: 8, borderRadius: "50%",
              background: isTalking ? "#fff" : "#444",
              animation: isTalking ? "pulse 0.8s infinite" : "none",
            }} />
            <span style={{ fontSize: 11, fontWeight: "bold", letterSpacing: 2, color: isTalking ? "#fff" : "#444" }}>
              {isTalking ? "ON AIR" : "STANDBY"}
            </span>
          </div>

          {/* Mode toggle */}
          <button
            onClick={() => setPttMode(p => !p)}
            style={{
              background: "transparent",
              border: "1px solid #333",
              borderRadius: 4,
              padding: "4px 12px",
              cursor: "pointer",
              color: "#888",
              fontSize: 11,
              letterSpacing: 2,
            }}
          >
            MODO: {pttMode ? "PTT ⬛ TIENI" : "TOGGLE ⬛ TOCCA"}
          </button>

          {/* Master mute */}
          <button
            onClick={() => {
              setMasterMute(p => !p);
              if (!masterMute) setActiveChannels(new Set());
            }}
            style={{
              background: masterMute ? "#FF1744" : "transparent",
              border: `1px solid ${masterMute ? "#FF1744" : "#333"}`,
              borderRadius: 4,
              padding: "4px 12px",
              cursor: "pointer",
              color: masterMute ? "#fff" : "#888",
              fontSize: 11,
              letterSpacing: 2,
              fontWeight: masterMute ? "bold" : "normal",
            }}
          >
            {masterMute ? "🔇 MUTE ON" : "🔊 MUTE OFF"}
          </button>
        </div>
      </div>

      {/* Channel Grid */}
      <div style={{
        display: "grid",
        gridTemplateColumns: "repeat(3, 1fr)",
        gap: 12,
        marginBottom: 20,
        width: "100%",
        maxWidth: 600,
      }}>
        {CHANNELS.map(ch => {
          const isActive = activeChannels.has(ch.id);
          return (
            <button
              key={ch.id}
              onMouseDown={() => pttMode ? activateChannel(ch.id) : toggleChannel(ch.id)}
              onMouseUp={() => pttMode && deactivateChannel(ch.id)}
              onMouseLeave={() => pttMode && deactivateChannel(ch.id)}
              onTouchStart={(e) => { e.preventDefault(); pttMode ? activateChannel(ch.id) : toggleChannel(ch.id); }}
              onTouchEnd={() => pttMode && deactivateChannel(ch.id)}
              style={{
                position: "relative",
                background: isActive ? ch.color + "22" : "#111",
                border: `2px solid ${isActive ? ch.color : "#2a2a2a"}`,
                borderRadius: 8,
                padding: "20px 12px",
                cursor: "pointer",
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: 6,
                transition: "all 0.08s",
                boxShadow: isActive ? `0 0 30px ${ch.color}44, inset 0 0 20px ${ch.color}11` : "none",
                transform: isActive ? "scale(0.97)" : "scale(1)",
                outline: "none",
              }}
            >
              {/* Key badge */}
              <div style={{
                position: "absolute",
                top: 6, right: 8,
                fontSize: 10,
                color: isActive ? ch.color : "#444",
                letterSpacing: 1,
                fontWeight: "bold",
              }}>
                [{ch.key}]
              </div>

              {/* Active dot */}
              {isActive && (
                <div style={{
                  position: "absolute",
                  top: 6, left: 8,
                  width: 8, height: 8,
                  borderRadius: "50%",
                  background: ch.color,
                  boxShadow: `0 0 8px ${ch.color}`,
                }} />
              )}

              <div style={{ fontSize: 28 }}>{ch.icon}</div>
              <div style={{
                fontSize: 13,
                fontWeight: "bold",
                letterSpacing: 2,
                color: isActive ? ch.color : "#ccc",
              }}>
                {ch.name}
              </div>
              <div style={{
                fontSize: 10,
                color: "#555",
                letterSpacing: 1,
              }}>
                {ch.shortDesc}
              </div>

              {isActive && (
                <div style={{
                  fontSize: 10,
                  color: ch.color,
                  letterSpacing: 2,
                  fontWeight: "bold",
                  animation: "blink 0.6s infinite",
                }}>
                  ● TX
                </div>
              )}
            </button>
          );
        })}
      </div>

      {/* Log */}
      <div style={{
        width: "100%",
        maxWidth: 600,
        background: "#0d0d0d",
        border: "1px solid #1e1e1e",
        borderRadius: 8,
        overflow: "hidden",
      }}>
        <div style={{
          background: "#141414",
          padding: "6px 14px",
          fontSize: 10,
          letterSpacing: 3,
          color: "#444",
          borderBottom: "1px solid #1e1e1e",
        }}>
          LOG TRASMISSIONI
        </div>
        <div
          ref={logRef}
          style={{
            height: 100,
            overflowY: "auto",
            padding: "8px 14px",
            display: "flex",
            flexDirection: "column",
            gap: 3,
          }}
        >
          {log.length === 0 && (
            <div style={{ color: "#333", fontSize: 11, letterSpacing: 1 }}>
              In attesa di trasmissioni...
            </div>
          )}
          {log.map((entry, i) => (
            <div key={i} style={{ display: "flex", gap: 10, fontSize: 11 }}>
              <span style={{ color: "#444", minWidth: 80 }}>{entry.time}</span>
              <span style={{ color: entry.color }}>{entry.msg}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Footer hint */}
      <div style={{
        marginTop: 16, fontSize: 10, color: "#333",
        letterSpacing: 2, textAlign: "center",
      }}>
        TASTI 1–6 O TASTIERINO NUMERICO • CLICK O TOCCO SU MOBILE
      </div>

      <style>{`
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
        @keyframes blink { 0%,100%{opacity:1} 50%{opacity:0.3} }
        button:focus { outline: none; }
        ::-webkit-scrollbar { width: 4px; }
        ::-webkit-scrollbar-track { background: #0d0d0d; }
        ::-webkit-scrollbar-thumb { background: #2a2a2a; border-radius: 2px; }
      `}</style>
    </div>
  );
}
