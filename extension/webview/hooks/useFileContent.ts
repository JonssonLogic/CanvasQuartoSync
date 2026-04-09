import { useState, useEffect } from 'react';

interface FileContent {
  content: string;
  filePath: string;
  imageMap: Record<string, string>;
}

/**
 * Listens for postMessage updates from the extension host.
 * Returns the latest file content, path, and image map.
 */
export function useFileContent() {
  const [fileContent, setFileContent] = useState<FileContent | null>(null);

  useEffect(() => {
    const handler = (event: MessageEvent) => {
      const msg = event.data;
      if (msg.type === 'updateContent') {
        setFileContent({
          content: msg.content,
          filePath: msg.filePath,
          imageMap: msg.imageMap || {},
        });
      }
    };

    window.addEventListener('message', handler);
    return () => window.removeEventListener('message', handler);
  }, []);

  return fileContent;
}
