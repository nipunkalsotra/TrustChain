import Link from "next/link";
export default function NotFound() {
  return (
    <main
      style={{
        minHeight: "100vh",
        display: "grid",
        placeItems: "center",
        background: "#171c29",
        color: "#e8ecff",
        fontFamily: "Arial, sans-serif",
        padding: 30,
      }}
    >
      <div>
        <p style={{ color: "#a6b3ff", fontSize: 12, marginBottom: 15 }}>
          404 / PAGE NOT FOUND
        </p>
        <h1 style={{ fontSize: 36, marginBottom: 16 }}>
          Let’s get you back on track.
        </h1>
        <p style={{ color: "#a1aac0", marginBottom: 30 }}>
          This page may have moved or the address may be incorrect.
        </p>
        <Link
          href="/dashboard"
          style={{
            padding: "12px 20px",
            background: "#5368d8",
            borderRadius: 7,
          }}
        >
          Open your workspace →
        </Link>
      </div>
    </main>
  );
}
