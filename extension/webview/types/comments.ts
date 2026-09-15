export interface Comment {
  id: string;
  section: string;
  paragraph: number;
  /** First ~40 chars of the paragraph, normalized — stable fingerprint even if paragraph index shifts */
  paraStart?: string;
  targetText: string;
  /** Character offset of targetText within the paragraph text */
  targetOffsetInPara?: number;
  contextBefore: string;
  contextAfter: string;
  body: string;
  createdAt: string;
  updatedAt: string;
  orphaned?: boolean;
  /** Resolved character offset in clean content (set by anchorComments) */
  _offset?: number;
}
