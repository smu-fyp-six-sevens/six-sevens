// "font_consistency_tool" -> "Font consistency"
export function formatCheck(tool: string | null | undefined): string {
  if (!tool) return 'Check';
  const words = tool.replace(/_tool$/, '').replace(/_/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}
