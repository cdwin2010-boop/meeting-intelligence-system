/*
 * 사람 원형 아바타(28px): avatar 바탕 + 이름 첫 글자. 이름은 옆에 글자로 항상 함께 보인다.
 * 첫 글자는 가상 요소(data-initial)로 그려 셀의 텍스트 내용을 바꾸지 않는다(스크린리더·텍스트 검사에 중복 글자가 없다).
 */
export function Avatar({ name }: { name: string }) {
  const initial = Array.from(name.trim())[0] ?? "?";
  return (
    <span
      aria-hidden="true"
      data-initial={initial}
      className="inline-flex size-7 shrink-0 items-center justify-center rounded-full bg-mn-avatar text-[13px] font-semibold leading-5 text-mn-text after:content-[attr(data-initial)]"
    />
  );
}

/** 아바타 + 이름 한 줄(표 셀용). 이름이 길면 줄여서 보이고, wrap 이면 줄바꿈해 전부 보인다 */
export function PersonName({ name, className = "", wrap = false }: { name: string; className?: string; wrap?: boolean }) {
  return (
    <span className={`inline-flex max-w-full min-w-0 items-center gap-2 align-middle ${className}`}>
      <Avatar name={name} />
      <span className={wrap ? "[overflow-wrap:anywhere]" : "min-w-0 truncate"}>{name}</span>
    </span>
  );
}
