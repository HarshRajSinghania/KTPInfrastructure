import sys
p='CHANGELOG.md'
txt=open(p,encoding='utf-8',newline='\n').read()
assert '\r\n' not in txt, 'CRLF present - aborting'
lines=txt.split('\n')
out=[];i=0;regions=0
while i<len(lines):
    if lines[i].startswith('<<<<<<<'):
        j=i+1;ours=[]
        while not lines[j]=='=======': ours.append(lines[j]); j+=1
        k=j+1;theirs=[]
        while not lines[k].startswith('>>>>>>>'): theirs.append(lines[k]); k+=1
        while ours and ours[-1]=='': ours.pop()
        while theirs and theirs[-1]=='': theirs.pop()
        out+=ours+['']+theirs
        regions+=1; i=k+1
    else:
        out.append(lines[i]); i+=1
open(p,'w',encoding='utf-8',newline='\n').write('\n'.join(out))
print(f'  regions resolved: {regions}')
