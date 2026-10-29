export const MAX_SLIDE_BYTES = 2 * 1024 * 1024;
export const MAX_SLIDE_SIDE = 4096;
export const MAX_SLIDE_PIXELS = 8_000_000;

/** Read dimensions before asking the browser to decode pixels. The server validates again. */
export function validateSlideImage(bytes: Uint8Array, mime: string): { width: number; height: number } {
  if (!bytes.length || bytes.length > MAX_SLIDE_BYTES) throw new Error("Choose a PNG or JPEG no larger than 2 MiB.");
  let width = 0;
  let height = 0;
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const png = bytes.length >= 24 && [137, 80, 78, 71, 13, 10, 26, 10].every((n, i) => bytes[i] === n);
  const jpeg = bytes[0] === 0xff && bytes[1] === 0xd8;
  if (png && mime === "image/png") {
    if (view.getUint32(12) !== 0x49484452 || view.getUint32(8) !== 13) throw new Error("This PNG header could not be read. Export a new image and try again.");
    width = view.getUint32(16);
    height = view.getUint32(20);
    // An APNG is not a single slide. Chunk bounds also prevent truncated headers.
    for (let offset = 8; offset + 12 <= bytes.length;) {
      const size = view.getUint32(offset);
      if (offset + size + 12 > bytes.length) throw new Error("This PNG appears incomplete. Export it again.");
      if (view.getUint32(offset + 4) === 0x6163544c) throw new Error("Animated images are not supported. Choose one still PNG or JPEG.");
      offset += size + 12;
    }
  } else if (jpeg && mime === "image/jpeg") {
    // JPEG stores dimensions in a start-of-frame marker, before the compressed image data.
    for (let offset = 2; offset + 4 <= bytes.length;) {
      if (bytes[offset++] !== 0xff) break;
      while (bytes[offset] === 0xff) offset++;
      const marker = bytes[offset++];
      if (marker === 0xda || marker === 0xd9) break;
      if (marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) continue;
      if (offset + 2 > bytes.length) break;
      const size = view.getUint16(offset);
      if (size < 2 || offset + size > bytes.length) break;
      if ([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf].includes(marker) && size >= 8) {
        height = view.getUint16(offset + 3);
        width = view.getUint16(offset + 5);
        break;
      }
      offset += size;
    }
  } else {
    throw new Error("Choose a PNG or JPEG image. PDFs, SVGs and other formats are not supported.");
  }
  if (!width || !height) throw new Error("The image dimensions could not be read. Export a new PNG or JPEG.");
  if (Math.min(width, height) < 32 || Math.max(width, height) > MAX_SLIDE_SIDE || width * height > MAX_SLIDE_PIXELS) {
    throw new Error("Use an image 32–4,096 pixels per side and no more than 8 megapixels. Crop or resize the slide first.");
  }
  return { width, height };
}

export function slideDataUrl(bytes: Uint8Array, mime: string): string {
  const chunks: string[] = [];
  for (let i = 0; i < bytes.length; i += 8192) chunks.push(String.fromCharCode(...bytes.subarray(i, i + 8192)));
  return `data:${mime};base64,${btoa(chunks.join(""))}`;
}
