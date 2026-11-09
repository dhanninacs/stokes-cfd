/** Scene features after the optional reduced-detail viewport profile is applied. */
export interface DisplayQualitySettings {
  lowDetail?: boolean;
  axisLines?: boolean;
  edges?: boolean;
  shadow?: boolean;
  reflections?: boolean;
}

/**
 * Low detail reduces rendering overhead without changing the solver mesh or result data. This is
 * intentionally an honest quality profile rather than geometry decimation: the current renderer
 * has no decimated LOD meshes to switch to.
 */
export function effectiveDisplayQuality(settings: DisplayQualitySettings) {
  const reduced = settings.lowDetail === true;
  return {
    axes: settings.axisLines !== false,
    edges: !reduced && settings.edges !== false,
    shadow: !reduced && settings.shadow !== false,
    reflections: !reduced && settings.reflections !== false,
  };
}
