import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const root = new URL("../src/institution_lab/observer/assets/", import.meta.url);
mkdirSync(root, { recursive: true });
copyFileSync(
  fileURLToPath(new URL("node_modules/cytoscape/dist/cytoscape.min.js", import.meta.url)),
  fileURLToPath(new URL("cytoscape.min.js", root)),
);
copyFileSync(
  fileURLToPath(new URL("node_modules/cytoscape/LICENSE", import.meta.url)),
  fileURLToPath(new URL("CYTOSCAPE-LICENSE", root)),
);
for (const [packageName, notice] of [
  ["@astryxdesign/theme-neutral", "ASTRYX-THEME-LICENSE"],
  ["react", "REACT-LICENSE"],
]) {
  copyFileSync(
    fileURLToPath(new URL(`node_modules/${packageName}/LICENSE`, import.meta.url)),
    fileURLToPath(new URL(notice, root)),
  );
}
for (const font of ["dm-sans", "manrope"]) {
  copyFileSync(
    new URL(`node_modules/@fontsource-variable/${font}/LICENSE`, import.meta.url),
    new URL(`${font.toUpperCase()}-FONT-LICENSE`, root),
  );
}
const astryx = JSON.parse(readFileSync(new URL("node_modules/@astryxdesign/core/package.json", import.meta.url), "utf8"));
writeFileSync(new URL("ASTRYX-CORE-NOTICE.txt", root),
  `${astryx.name} ${astryx.version}\nLicense: ${astryx.license}\nhttps://astryx.atmeta.com/\n`);
