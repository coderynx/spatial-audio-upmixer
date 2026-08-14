export type UploadItem = { file: File; path: string };

interface LegacyFileSystemEntry {
  isFile: boolean;
  isDirectory: boolean;
  name: string;
  fullPath: string;
}
interface LegacyFileSystemFileEntry extends LegacyFileSystemEntry {
  file: (
    success: (file: File) => void,
    error?: (error: DOMException) => void,
  ) => void;
}
interface LegacyFileSystemDirectoryReader {
  readEntries: (
    success: (entries: LegacyFileSystemEntry[]) => void,
    error?: (error: DOMException) => void,
  ) => void;
}
interface LegacyFileSystemDirectoryEntry extends LegacyFileSystemEntry {
  createReader: () => LegacyFileSystemDirectoryReader;
}

function entryFiles(entry: LegacyFileSystemEntry): Promise<UploadItem[]> {
  if (entry.isFile)
    return new Promise((resolve, reject) =>
      (entry as LegacyFileSystemFileEntry).file(
        (file) => resolve([{ file, path: entry.fullPath.replace(/^\//, "") }]),
        reject,
      ),
    );
  return new Promise((resolve, reject) => {
    const reader = (entry as LegacyFileSystemDirectoryEntry).createReader();
    const entries: LegacyFileSystemEntry[] = [];
    const read = () =>
      reader.readEntries(async (batch) => {
        if (batch.length) {
          entries.push(...batch);
          read();
          return;
        }
        try {
          resolve((await Promise.all(entries.map(entryFiles))).flat());
        } catch (error) {
          reject(error);
        }
      }, reject);
    read();
  });
}

export async function droppedItems(
  event: React.DragEvent,
): Promise<UploadItem[]> {
  const entries = Array.from(event.dataTransfer.items)
    .map((item) =>
      (
        item as unknown as {
          webkitGetAsEntry?: () => LegacyFileSystemEntry | null;
        }
      ).webkitGetAsEntry?.(),
    )
    .filter((entry): entry is LegacyFileSystemEntry => entry != null);
  if (entries.length)
    return (await Promise.all(entries.map(entryFiles))).flat();
  return Array.from(event.dataTransfer.files).map((file) => ({
    file,
    path: file.webkitRelativePath || file.name,
  }));
}
