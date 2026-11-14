"use client";
import { useEffect, useState } from "react";
import { Pause, Play } from "lucide-react";

export function MotionControl() {
  const [paused, setPaused] = useState(false);
  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    function sync() {
      const preference = localStorage.getItem("tc_motion");
      const value = preference ? preference === "paused" : media.matches;
      document.documentElement.dataset.motion = value ? "paused" : "full";
      setPaused(value);
    }
    sync();
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, []);
  return <button className="motion-control" aria-label={paused ? "Resume animations" : "Pause animations"} aria-pressed={paused} onClick={() => {
    const next = !paused;
    setPaused(next);
    localStorage.setItem("tc_motion", next ? "paused" : "full");
    document.documentElement.dataset.motion = next ? "paused" : "full";
  }}>{paused ? <Play size={12} /> : <Pause size={12} />}<span>Motion {paused ? "off" : "on"}</span></button>;
}
