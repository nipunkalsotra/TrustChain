import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    // ── React Three Fiber scene code ────────────────────────────────────────
    // The React Compiler's `react-hooks/immutability` and `react-hooks/refs`
    // rules assume every value a component holds is either immutable or only
    // touched during render/commit. React Three Fiber's model is the opposite
    // by design: `useFrame` runs OUTSIDE React's render cycle, on the WebGL
    // render loop, and its entire job is to mutate long-lived objects in place
    // — shader uniforms, a reused Vector3, an Object3D used to compose
    // instance matrices. That is not incidental style here; allocating those
    // per frame (thousands of times per second across the particle, chain and
    // block systems) is exactly the garbage-collection stutter this landing
    // page's spec makes a release requirement.
    //
    // Scoped to this directory only, so the rules keep their full force over
    // every ordinary React component in the app, including the landing page's
    // own DOM layer in components/landing/components/.
    files: ["components/landing/three/**/*.{ts,tsx}"],
    rules: {
      "react-hooks/immutability": "off",
      "react-hooks/refs": "off",
    },
  },
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
]);

export default eslintConfig;
