import { readFile, writeFile, mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";
import pngToIco from "png-to-ico";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..", "..");
const SVG_PATH = path.join(ROOT, "frontend", "public", "icon.svg");
const BUILD_DIR = path.resolve(__dirname, "..", "build");

const ICO_SIZES = [16, 24, 32, 48, 64, 128, 256];
const PNG_SIZE = 256;

async function main() {
  await mkdir(BUILD_DIR, { recursive: true });
  const svgBuffer = await readFile(SVG_PATH);

  const pngBuffer = await sharp(svgBuffer, { density: 384 })
    .resize(PNG_SIZE, PNG_SIZE)
    .png()
    .toBuffer();
  await writeFile(path.join(BUILD_DIR, "icon.png"), pngBuffer);

  const pngBuffers = await Promise.all(
    ICO_SIZES.map((size) =>
      sharp(svgBuffer, { density: 384 }).resize(size, size).png().toBuffer()
    )
  );
  const icoBuffer = await pngToIco(pngBuffers);
  await writeFile(path.join(BUILD_DIR, "icon.ico"), icoBuffer);

  console.log("Icons written to", BUILD_DIR);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
