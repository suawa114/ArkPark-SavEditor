export function weaponPosition(index: number): string {
  return ['一号位', '二号位'][index] ?? `${index + 1} 号位`
}
